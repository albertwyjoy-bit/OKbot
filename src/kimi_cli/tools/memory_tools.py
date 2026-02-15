"""
Memory Tools - MCP-style tools for memory retrieval and storage

参考 claude-mem 设计，提供四个核心工具：
- SearchMemory: 搜索记忆
- TimelineMemory: 获取时间线上下文
- GetObservations: 批量获取观测详情
- SaveMemory: 保存手动记忆
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import override

from kosong.tooling import CallableTool2, ToolReturnValue
from pydantic import BaseModel, Field

from kimi_cli.soul.agent import Runtime
from kimi_cli.memory.types import SearchFilters, ObservationType, ObservationInput
from kimi_cli.tools.utils import ToolResultBuilder
from kimi_cli.utils.logging import logger


# ============== Tool 1: SearchMemory ==============

class SearchMemoryParams(BaseModel):
    query: str = Field(description="Full-text search query (supports AND, OR, NOT, phrase searches)")
    limit: int = Field(default=20, description="Maximum number of results", ge=1, le=100)
    offset: int = Field(default=0, description="Skip first N results for pagination", ge=0)
    project: str | None = Field(default=None, description="Filter by project name/path")
    type: str | None = Field(
        default=None,
        description="Filter by observation type: bugfix, feature, refactor, change, discovery, decision"
    )
    obs_type: str | None = Field(
        default=None,
        description="Filter by record type: observation, session/summary, prompt"
    )
    dateStart: str | None = Field(
        default=None,
        description="Filter by start date (YYYY-MM-DD)"
    )
    dateEnd: str | None = Field(
        default=None,
        description="Filter by end date (YYYY-MM-DD)"
    )
    orderBy: str = Field(
        default="relevance",
        description="Sort order: date_desc, date_asc, relevance"
    )


class SearchMemory(CallableTool2[SearchMemoryParams]):
    """
    Step 1: Search memory index with query
    
    Returns compact index with IDs (~50-100 tokens/result).
    Use TimelineMemory or GetObservations for full details.
    """
    name: str = "SearchMemory"
    params: type[SearchMemoryParams] = SearchMemoryParams

    def __init__(self, runtime: Runtime):
        super().__init__(
            description="""Search memory for relevant past observations.

Use this as Step 1 of the 3-layer memory retrieval workflow:
1. SearchMemory(query) → Get index with IDs
2. TimelineMemory(anchor=id) → Get context around interesting results  
3. GetObservations(ids=[...]) → Fetch full details only for relevant items

This approach saves ~10x tokens by filtering before fetching full details."""
        )
        self._runtime = runtime

    @override
    async def __call__(self, params: SearchMemoryParams) -> ToolReturnValue:
        builder = ToolResultBuilder()
        memory = self._runtime.memory_agent
        
        if not memory:
            return builder.error("Memory system not available", brief="Memory not available")
        
        try:
            # Build filters
            filters = SearchFilters()
            project = params.project or memory.project
            if project:
                filters.project = project
            
            # Handle observation type filter (params.type)
            if params.type:
                try:
                    filters.types = [ObservationType(params.type.lower())]
                except ValueError:
                    pass  # Invalid type, ignore filter
            
            # Handle date range filters
            if params.dateStart:
                try:
                    filters.date_after = datetime.strptime(params.dateStart, "%Y-%m-%d")
                except ValueError:
                    pass
            if params.dateEnd:
                try:
                    # Add 1 day to include the end date fully
                    from datetime import timedelta
                    end_date = datetime.strptime(params.dateEnd, "%Y-%m-%d") + timedelta(days=1)
                    filters.date_before = end_date
                except ValueError:
                    pass
            
            results = []
            
            # Determine which record types to search based on obs_type filter
            search_observations = True
            search_summaries = False
            search_prompts = False
            
            if params.obs_type:
                obs_type_lower = params.obs_type.lower()
                if obs_type_lower == "observation":
                    search_summaries = False
                    search_prompts = False
                elif obs_type_lower in ("session", "summary"):
                    search_observations = False
                    search_summaries = True
                elif obs_type_lower == "prompt":
                    search_observations = False
                    search_prompts = True
            
            # Search observations
            if search_observations:
                obs_results = await memory.searcher.search_observations(
                    query=params.query,
                    filters=filters,
                    top_k=params.limit + params.offset
                )
                
                for r in obs_results[params.offset:params.offset + params.limit]:
                    obs = r.observation
                    results.append({
                        "id": obs.id,
                        "type": obs.type.value,
                        "item_type": "observation",
                        "title": obs.title,
                        "concepts": obs.concepts,
                        "prompt_number": obs.prompt_number,
                        "created_at": obs.created_at.isoformat() if hasattr(obs.created_at, 'isoformat') else str(obs.created_at),
                        "score": round(r.score, 3),
                    })
            
            # Search summaries if requested or no specific type filter
            if search_summaries or (not params.obs_type and params.query):
                summary_results = await self._search_summaries(memory, params, filters)
                for item in summary_results:
                    results.append(item)
            
            # Search prompts if requested or no specific type filter  
            if search_prompts or (not params.obs_type and params.query):
                prompt_results = await self._search_prompts(memory, params, filters)
                for item in prompt_results:
                    results.append(item)
            
            # Sort results based on orderBy
            if params.orderBy == "date_desc":
                results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            elif params.orderBy == "date_asc":
                results.sort(key=lambda x: x.get("created_at", ""))
            elif params.orderBy == "relevance":
                results.sort(key=lambda x: x.get("score", 0), reverse=True)
            
            # Apply limit after combining results
            results = results[:params.limit]
            
            result_data = {
                "query": params.query,
                "count": len(results),
                "offset": params.offset,
                "limit": params.limit,
                "filters": {
                    "project": project,
                    "type": params.type,
                    "obs_type": params.obs_type,
                    "dateStart": params.dateStart,
                    "dateEnd": params.dateEnd,
                },
                "results": results,
                "hint": "Use TimelineMemory(anchor=id) or GetObservations(ids=[...]) for full details"
            }
            builder.extras(**result_data)
            return builder.ok(f"Found {len(results)} results for query '{params.query}'.")
            
        except Exception as e:
            logger.error("Memory search failed: {e}", e=e)
            return builder.error(f"Search failed: {e}", brief="Search failed")
    
    async def _search_summaries(self, memory, params: SearchMemoryParams, filters: SearchFilters) -> list[dict]:
        """Search session summaries"""
        results = []
        try:
            # Use FTS to search summaries
            fts_results = memory.db.fts_search_summaries(params.query, limit=params.limit)
            
            for rowid, rank in fts_results:
                summary = memory.db.get_summary(rowid)
                if summary:
                    # Check project filter
                    if filters.project and summary.project != filters.project:
                        continue
                    results.append({
                        "id": f"S{summary.id}",
                        "type": "summary",
                        "item_type": "summary",
                        "title": summary.request[:100] if summary.request else "Session summary",
                        "request": summary.request,
                        "prompt_number": summary.prompt_number,
                        "created_at": summary.created_at.isoformat() if hasattr(summary.created_at, 'isoformat') else str(summary.created_at),
                        "score": round(1.0 / (1.0 + abs(rank)), 3) if rank else 0.5,
                    })
        except Exception as e:
            logger.debug(f"Summary search error: {e}")
        return results
    
    async def _search_prompts(self, memory, params: SearchMemoryParams, filters: SearchFilters) -> list[dict]:
        """Search user prompts"""
        results = []
        try:
            # Use FTS to search prompts
            fts_results = memory.db.fts_search_prompts(params.query, limit=params.limit)
            
            for rowid, rank in fts_results:
                prompt = memory.db.get_prompt(rowid)
                if prompt:
                    # Check project filter
                    if filters.project and prompt.project != filters.project:
                        continue
                    results.append({
                        "id": f"P{prompt.id}",
                        "type": "prompt",
                        "item_type": "prompt",
                        "title": prompt.prompt_text[:100] + "..." if len(prompt.prompt_text) > 100 else prompt.prompt_text,
                        "prompt_text": prompt.prompt_text[:200] + "..." if len(prompt.prompt_text) > 200 else prompt.prompt_text,
                        "prompt_number": prompt.prompt_number,
                        "created_at": prompt.created_at.isoformat() if hasattr(prompt.created_at, 'isoformat') else str(prompt.created_at),
                        "score": round(1.0 / (1.0 + abs(rank)), 3) if rank else 0.5,
                    })
        except Exception as e:
            logger.debug(f"Prompt search error: {e}")
        return results


# ============== Tool 2: TimelineMemory ==============

class TimelineMemoryParams(BaseModel):
    anchor: str | None = Field(
        default=None,
        description="Anchor point: observation ID, summary ID ('S123'), prompt ID ('P456'), or ISO timestamp. Optional if query provided."
    )
    query: str | None = Field(
        default=None,
        description="Search query to find anchor automatically (optional if anchor provided)"
    )
    depth_before: int = Field(default=3, description="Number of records before anchor", ge=0, le=10)
    depth_after: int = Field(default=3, description="Number of records after anchor", ge=0, le=10)
    project: str | None = Field(default=None, description="Filter by project name/path")


class TimelineMemory(CallableTool2[TimelineMemoryParams]):
    """
    Step 2: Get unified timeline context around an anchor point
    
    Returns chronological context mixing observations and summaries.
    Shows what was happening at that time (before/after).
    """
    name: str = "TimelineMemory"
    params: type[TimelineMemoryParams] = TimelineMemoryParams

    def __init__(self, runtime: Runtime):
        super().__init__(
            description="""Get unified timeline context around a specific anchor point.

Use this as Step 2 after SearchMemory finds interesting results:
1. SearchMemory(query) → Get list of observation IDs
2. TimelineMemory(anchor=id) → See chronological context (observations + summaries + prompts)
3. GetObservations(ids=[...]) → Fetch full details for relevant items

Anchor formats:
- Observation ID: "123" or 123
- Summary ID: "S456" (summary/session anchor)
- Prompt ID: "P789" (user prompt anchor)
- Timestamp: "2024-01-15T10:30:00"

Returns unified timeline with observations, summaries, and prompts sorted chronologically.
Timeline item types: 'observation' | 'summary' | 'prompt'"""
        )
        self._runtime = runtime

    @override
    async def __call__(self, params: TimelineMemoryParams) -> ToolReturnValue:
        builder = ToolResultBuilder()
        memory = self._runtime.memory_agent
        
        if not memory:
            return builder.error("Memory system not available", brief="Memory not available")
        
        try:
            project = params.project or memory.project
            if not project:
                return builder.error("No project specified", brief="No project")
            
            # Determine anchor: use provided anchor or search for it via query
            anchor = params.anchor
            if not anchor and params.query:
                # Search for anchor automatically
                anchor = await self._find_anchor_by_query(memory, params.query, project)
                if not anchor:
                    return builder.error(f"Could not find anchor for query: {params.query}", brief="Anchor not found")
            
            if not anchor:
                return builder.error("Either anchor or query must be provided", brief="Missing anchor/query")
            
            # Parse anchor
            anchor_id, anchor_epoch, anchor_type = await self._parse_anchor(
                anchor, memory, project
            )
            
            if anchor_id is None:
                return builder.error(f"Invalid anchor: {anchor}", brief="Invalid anchor")
            
            # Get all data for the project (observations + summaries + prompts)
            obs_list = memory.db.get_observations_by_project(project, limit=100)
            summaries = memory.db.get_summaries_by_project(project, limit=20)
            prompts = memory.db.get_prompts_by_project(project, limit=50)
            
            # Build unified timeline items
            timeline_items: list[dict] = []
            
            # Add observations
            for obs in obs_list:
                timeline_items.append({
                    "item_type": "observation",
                    "id": obs.id,
                    "type": obs.type.value,
                    "title": obs.title,
                    "prompt_number": obs.prompt_number,
                    "created_at": obs.created_at.timestamp() if hasattr(obs.created_at, 'timestamp') else 0,
                    "is_anchor": self._is_anchor(obs.id, anchor_id, anchor_type, "observation"),
                })
            
            # Add summaries (sessions)
            for summary in summaries:
                timeline_items.append({
                    "item_type": "summary",
                    "id": f"S{summary.id}",
                    "request": summary.request,
                    "completed": summary.completed,
                    "prompt_number": summary.prompt_number,
                    "created_at": summary.created_at.timestamp() if hasattr(summary.created_at, 'timestamp') else 0,
                    "is_anchor": self._is_anchor(summary.id, anchor_id, anchor_type, "summary"),
                })
            
            # Add prompts
            for prompt in prompts:
                timeline_items.append({
                    "item_type": "prompt",
                    "id": f"P{prompt.id}",
                    "prompt_text": prompt.prompt_text[:200] + "..." if len(prompt.prompt_text) > 200 else prompt.prompt_text,
                    "prompt_number": prompt.prompt_number,
                    "created_at": prompt.created_at.timestamp() if hasattr(prompt.created_at, 'timestamp') else 0,
                    "is_anchor": self._is_anchor(prompt.id, anchor_id, anchor_type, "prompt"),
                })
            
            # Sort by created_at (chronologically)
            timeline_items.sort(key=lambda x: x["created_at"])
            
            # Find anchor index
            anchor_idx = None
            for i, item in enumerate(timeline_items):
                if item["is_anchor"]:
                    anchor_idx = i
                    break
            
            if anchor_idx is None:
                # Anchor not in timeline, return all
                result_data = {
                    "anchor": params.anchor,
                    "anchor_type": anchor_type,
                    "project": project,
                    "timeline": timeline_items,
                    "count": len(timeline_items),
                }
                builder.extras(**result_data)
                return builder.ok(f"Timeline for anchor '{params.anchor}' ({anchor_type}) with {len(timeline_items)} items.")
            
            # Get context window around anchor
            start_idx = max(0, anchor_idx - params.depth_before)
            end_idx = min(len(timeline_items), anchor_idx + params.depth_after + 1)
            context_items = timeline_items[start_idx:end_idx]
            
            anchor_title = await self._get_anchor_title(anchor_id, anchor_type, memory)
            result_data = {
                "anchor": params.anchor,
                "anchor_type": anchor_type,
                "anchor_title": anchor_title,
                "project": project,
                "timeline": context_items,
                "count": len(context_items),
                "window": {"before": params.depth_before, "after": params.depth_after},
                "hint": "Use GetObservations(ids=[...]) for full details of interesting observation items"
            }
            builder.extras(**result_data)
            return builder.ok(f"Timeline context around '{anchor_title}' with {len(context_items)} items.")
            
        except Exception as e:
            logger.error("Timeline fetch failed: {e}", e=e)
            return builder.error(f"Timeline failed: {e}", brief="Timeline fetch failed")
    
    async def _find_anchor_by_query(self, memory, query: str, project: str) -> str | None:
        """Search for anchor using query, return best match ID"""
        try:
            # Search observations first
            filters = SearchFilters(project=project)
            results = await memory.searcher.search_observations(query=query, filters=filters, top_k=1)
            if results:
                return str(results[0].observation.id)
            
            # Try searching summaries
            fts_results = memory.db.fts_search_summaries(query, limit=1)
            if fts_results:
                return f"S{fts_results[0][0]}"
            
            # Try searching prompts
            prompt_results = memory.db.fts_search_prompts(query, limit=1)
            if prompt_results:
                return f"P{prompt_results[0][0]}"
            
            return None
        except Exception as e:
            logger.debug(f"Anchor search error: {e}")
            return None
    
    async def _parse_anchor(
        self, anchor: str, memory, project: str
    ) -> tuple[str | int | None, float | None, str]:
        """Parse anchor string and return (anchor_id, anchor_epoch, anchor_type)"""
        anchor = str(anchor).strip()
        
        # Try observation ID (numeric)
        if anchor.isdigit():
            obs_id = int(anchor)
            obs = memory.db.get_observation(obs_id)
            if obs:
                epoch = obs.created_at.timestamp() if hasattr(obs.created_at, 'timestamp') else 0
                return obs_id, epoch, "observation"
            return obs_id, None, "observation"
        
        # Try summary/session ID (S123 or #S123)
        if anchor.upper().startswith('S') or anchor.upper().startswith('#S'):
            session_id = anchor.upper().replace('#', '').replace('S', '')
            if session_id.isdigit():
                sum_id = int(session_id)
                summary = memory.db.get_summary(sum_id)
                if summary:
                    epoch = summary.created_at.timestamp() if hasattr(summary.created_at, 'timestamp') else 0
                    return sum_id, epoch, "summary"
                return f"S{sum_id}", None, "summary"
        
        # Try prompt ID (P123 or #P123)
        if anchor.upper().startswith('P') or anchor.upper().startswith('#P'):
            prompt_id_str = anchor.upper().replace('#', '').replace('P', '')
            if prompt_id_str.isdigit():
                prompt_id = int(prompt_id_str)
                prompt = memory.db.get_prompt(prompt_id)
                if prompt:
                    epoch = prompt.created_at.timestamp() if hasattr(prompt.created_at, 'timestamp') else 0
                    return prompt_id, epoch, "prompt"
                return f"P{prompt_id}", None, "prompt"
        
        # Try ISO timestamp
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(anchor.replace('Z', '+00:00'))
            return anchor, dt.timestamp(), "timestamp"
        except ValueError:
            pass
        
        return None, None, "unknown"
    
    def _is_anchor(
        self, item_id, anchor_id, anchor_type: str, item_type: str
    ) -> bool:
        """Check if this item is the anchor"""
        if anchor_type == "observation" and item_type == "observation":
            return item_id == anchor_id
        if anchor_type == "summary" and item_type == "summary":
            return item_id == anchor_id
        if anchor_type == "prompt" and item_type == "prompt":
            return item_id == anchor_id
        return False
    
    async def _get_anchor_title(
        self, anchor_id, anchor_type: str, memory
    ) -> str:
        """Get title/description of the anchor"""
        if anchor_type == "observation":
            obs = memory.db.get_observation(anchor_id)
            return obs.title if obs else "Unknown observation"
        elif anchor_type == "summary":
            summary = memory.db.get_summary(anchor_id)
            return summary.request if summary else "Unknown summary"
        elif anchor_type == "prompt":
            prompt = memory.db.get_prompt(anchor_id)
            text = prompt.prompt_text if prompt else "Unknown prompt"
            return text[:100] + "..." if len(text) > 100 else text
        return str(anchor_id)


# ============== Tool 3: GetObservations ==============

class GetObservationsParams(BaseModel):
    ids: list[int] = Field(description="Array of observation IDs to fetch full details for")
    orderBy: str = Field(
        default="date_desc",
        description="Sort order: date_desc, date_asc"
    )
    limit: int | None = Field(
        default=None,
        description="Maximum observations to return",
        ge=1
    )
    project: str | None = Field(
        default=None,
        description="Filter by project name/path"
    )


class GetObservations(CallableTool2[GetObservationsParams]):
    """
    Step 3: Fetch full details for specific observation IDs
    
    ALWAYS batch for 2+ items. Returns complete details (~500-1000 tokens/result).
    """
    name: str = "GetObservations"
    params: type[GetObservationsParams] = GetObservationsParams

    def __init__(self, runtime: Runtime):
        super().__init__(
            description="""Fetch full details for specific observation IDs.

Use this as Step 3 to get complete information:
1. SearchMemory(query) → Get observation IDs
2. TimelineMemory(anchor=id) → Find relevant context
3. GetObservations(ids=[123, 456]) → Fetch full details

ALWAYS batch IDs for 2+ items to minimize tool calls.
Full details include: narrative, facts, concepts, files, etc."""
        )
        self._runtime = runtime

    @override
    async def __call__(self, params: GetObservationsParams) -> ToolReturnValue:
        builder = ToolResultBuilder()
        memory = self._runtime.memory_agent
        
        if not memory:
            return builder.error("Memory system not available", brief="Memory not available")
        
        if not params.ids:
            return builder.error("No IDs provided", brief="No IDs")
        
        try:
            project_filter = params.project or memory.project
            results = []
            
            for obs_id in params.ids:
                obs = memory.db.get_observation(obs_id)
                if obs:
                    # Check project filter
                    if project_filter and obs.project != project_filter:
                        continue
                        
                    results.append({
                        "id": obs.id,
                        "type": obs.type.value,
                        "title": obs.title,
                        "subtitle": obs.subtitle,
                        "narrative": obs.narrative,
                        "facts": obs.facts,
                        "concepts": obs.concepts,
                        "files_read": obs.files_read,
                        "files_modified": obs.files_modified,
                        "tool_name": obs.tool_name,
                        "prompt_number": obs.prompt_number,
                        "created_at": obs.created_at.isoformat() if isinstance(obs.created_at, datetime) else str(obs.created_at),
                    })
            
            # Sort results based on orderBy
            if params.orderBy == "date_desc":
                results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            elif params.orderBy == "date_asc":
                results.sort(key=lambda x: x.get("created_at", ""))
            
            # Apply limit
            if params.limit:
                results = results[:params.limit]
            
            result_data = {
                "requested": len(params.ids),
                "found": len(results),
                "filters": {
                    "project": project_filter,
                    "orderBy": params.orderBy,
                },
                "observations": results,
            }
            builder.extras(**result_data)
            return builder.ok(f"Found {len(results)} observations out of {len(params.ids)} requested.")
            
        except Exception as e:
            logger.error("Get observations failed: {e}", e=e)
            return builder.error(f"Failed to fetch observations: {e}", brief="Failed to fetch observations")


# ============== Tool 4: SaveMemory ==============

class SaveMemoryParams(BaseModel):
    text: str = Field(description="Content to remember - the main information to save")
    title: str | None = Field(default=None, description="Short title (auto-generated from text if omitted)")
    concepts: list[str] | None = Field(default=None, description="Concept tags for searchability (auto-detected if omitted)")


class SaveMemory(CallableTool2[SaveMemoryParams]):
    """
    Save a manual memory/observation for semantic search
    
    Use this to remember important information, decisions, or findings.
    """
    name: str = "SaveMemory"
    params: type[SaveMemoryParams] = SaveMemoryParams

    def __init__(self, runtime: Runtime):
        super().__init__(
            description="""Save important information to memory for future retrieval.

Use this when:
- You discover something important that should be remembered
- You make a key decision that future sessions should know about
- You find a solution to a problem that might recur
- You want to bookmark important context

The memory will be searchable via SearchMemory(query) in future sessions."""
        )
        self._runtime = runtime

    @override
    async def __call__(self, params: SaveMemoryParams) -> ToolReturnValue:
        builder = ToolResultBuilder()
        memory = self._runtime.memory_agent
        
        if not memory:
            return builder.error("Memory system not available", brief="Memory not available")
        
        try:
            # Auto-generate title if not provided
            title = params.title
            if not title:
                title = params.text.split('.')[0][:50]
                if len(params.text) > 50:
                    title += "..."
            
            # Auto-detect concepts if not provided
            concepts = params.concepts
            if not concepts:
                concepts = []
                keywords = ["auth", "api", "database", "ui", "bug", "feature", "refactor", 
                           "config", "test", "deploy", "security", "performance", "error"]
                text_lower = params.text.lower()
                for kw in keywords:
                    if kw in text_lower:
                        concepts.append(kw)
            
            # Create observation
            obs_input = ObservationInput(
                session_id=self._runtime.session.id if self._runtime.session else "manual",
                project=memory.project or "/",
                type=ObservationType.DISCOVERY,
                title=title,
                narrative=params.text,
                concepts=concepts or ["manual"],
                prompt_number=0,
                discovery_tokens=0,
            )
            
            # Queue for saving
            await memory.queue_observation(obs_input, wait=True)
            
            result_data = {
                "status": "saved",
                "title": title,
                "project": memory.project or "/",
                "concepts": concepts or ["manual"],
                "hint": "This memory is now searchable via SearchMemory(query)"
            }
            builder.extras(**result_data)
            return builder.ok(f"Memory saved: '{title}' with concepts: {concepts or ['manual']}.")
            
        except Exception as e:
            logger.error("Save memory failed: {e}", e=e)
            return builder.error(f"Failed to save memory: {e}", brief="Save failed")
