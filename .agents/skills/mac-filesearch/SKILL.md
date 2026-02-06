---
name: mac-filesearch
description: "Use this skill when the user wants to search for files, applications, or documents on macOS. Triggers include: searching for files by name, finding applications, locating documents by content, finding recently modified files, or when the user mentions Spotlight, file search, or finding something on their Mac. This skill utilizes the system-native mdfind command (macOS Spotlight) for fast, indexed file search. Note: This tool is macOS ONLY and will not work on Linux or Windows."
license: Proprietary
---

# macOS File Search via Spotlight (mdfind)

## Platform Compatibility

⚠️ **macOS ONLY** ⚠️

This skill uses the `mdfind` command, which is exclusive to macOS. It leverages the system-wide Spotlight search index. This skill **will not work** on:
- Linux (use `find`, `locate`, or `fd` instead)
- Windows (use `dir`, `where`, or PowerShell `Get-ChildItem` instead)

## Overview

`mdfind` is a command-line interface to macOS Spotlight, the system-wide search technology. Unlike traditional file search commands like `find`, `mdfind` uses a pre-built index, making searches **extremely fast** even across the entire filesystem.

### Key Advantages
- **Speed**: Uses Spotlight index (searches millions of files in milliseconds)
- **Content search**: Can search inside file contents (PDFs, documents, code)
- **Metadata search**: Search by file type, creation date, author, etc.
- **Real-time**: Index updates automatically

## Quick Reference

| Task | Command |
|------|---------|
| Find by filename | `mdfind -name "filename"` |
| Find in specific folder | `mdfind -onlyin /path/to/folder "query"` |
| Find applications | `mdfind "kMDItemKind=='Application'"` |
| Find recent files | `mdfind -live "kMDItemFSContentChangeDate>$time.now(-3600)"` |
| Find by content | `mdfind "content of the file"` |

## Command Reference

### Basic Syntax

```bash
mdfind [options] query
```

### Common Options

| Option | Description | Example |
|--------|-------------|---------|
| `-name` | Match against file/directory names only | `mdfind -name "report"` |
| `-onlyin` | Restrict search to specific directory | `mdfind -onlyin ~/Documents "budget"` |
| `-live` | Continuously update results (live search) | `mdfind -live "project"` |
| `-count` | Return count only, not paths | `mdfind -count "*.pdf"` |
| `-s` | Interpret query as raw Spotlight query (no smart interpretation) | `mdfind -s "kMDItemContentType==public.image"` |
| `-0` | Separate results with null character (for xargs) | `mdfind -0 "*.log" \| xargs -0 ls -la` |

### Common Metadata Attributes

Spotlight indexes extensive metadata. Use these in queries:

| Attribute | Description | Example |
|-----------|-------------|---------|
| `kMDItemDisplayName` | File display name | `kMDItemDisplayName=='*report*'c` |
| `kMDItemFSName` | Actual filesystem name | `kMDItemFSName=='document.pdf'` |
| `kMDItemKind` | File type description | `kMDItemKind=='Application'` |
| `kMDItemContentType` | UTI content type | `kMDItemContentType==public.image` |
| `kMDItemContentTypeTree` | Content type hierarchy | `kMDItemContentTypeTree=='public.text'` |
| `kMDItemFSCreationDate` | Creation date | `kMDItemFSCreationDate>$time.today()` |
| `kMDItemFSContentChangeDate` | Modification date | `kMDItemFSContentChangeDate>$time.this_week()` |
| `kMDItemLastUsedDate` | Last opened date | `kMDItemLastUsedDate>$time.today(-7)` |
| `kMDItemAuthors` | Document authors | `kMDItemAuthors=='John Smith'` |
| `kMDItemTitle` | Document title | `kMDItemTitle=='Annual Report'` |

### Time Syntax

Time comparisons use special functions:

| Expression | Meaning |
|------------|---------|
| `$time.now()` | Current time |
| `$time.now(-3600)` | 1 hour ago (seconds) |
| `$time.today()` | Start of today |
| `$time.today(-1)` | Start of yesterday |
| `$time.this_week()` | Start of this week |
| `$time.this_month()` | Start of this month |

### String Matching Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `==` | Exact match | `kMDItemKind=='Application'` |
| `!=` | Not equal | `kMDItemKind!='Application'` |
| `=` | Prefix match | `kMDItemDisplayName='Project'` |
| `==*` | Case-insensitive | `kMDItemDisplayName=='report'c` |
| `==[c]` | Case-insensitive | `kMDItemDisplayName=='REPORT'c` |

## Typical Use Cases

### 1. Find Applications

```bash
# Find all applications
mdfind "kMDItemKind=='Application'"

# Find a specific app by name
mdfind "kMDItemKind=='Application' && kMDItemDisplayName=='*chrome*'c"

# Find recently used apps
mdfind "kMDItemKind=='Application' && kMDItemLastUsedDate>$time.today(-7)"
```

### 2. Find Documents by Type

```bash
# All PDFs
mdfind "kMDItemContentTypeTree=='public.pdf'"

# Images (any format)
mdfind "kMDItemContentTypeTree=='public.image'"

# Text files
mdfind "kMDItemContentTypeTree=='public.text'"

# Microsoft Word documents
mdfind "kMDItemContentType=='com.microsoft.word.doc'"
```

### 3. Search by Content

```bash
# Files containing specific text (searches content)
mdfind "quarterly financial report"

# PDFs with specific content
mdfind "kMDItemContentTypeTree=='public.pdf' && confidential"

# Code files containing a function
mdfind "def calculate_total"
```

### 4. Find Recent Files

```bash
# Modified in last hour
mdfind "kMDItemFSContentChangeDate>$time.now(-3600)"

# Created today
mdfind "kMDItemFSCreationDate>$time.today()"

# Accessed this week
mdfind "kMDItemLastUsedDate>$time.this_week()"

# Live monitoring of new files
mdfind -live "kMDItemFSCreationDate>$time.now(-60)"
```

### 5. Search Specific Locations

```bash
# Search only in Documents
mdfind -onlyin ~/Documents "budget"

# Search Downloads for PDFs
mdfind -onlyin ~/Downloads "kMDItemContentTypeTree=='public.pdf'"

# Search external drive
mdfind -onlyin /Volumes/ExternalDrive "project"
```

### 6. Find Files by Name Pattern

```bash
# Name contains "report" (case-insensitive)
mdfind -name "report"

# Exact name match
mdfind "kMDItemDisplayName=='Invoice.pdf'"

# Wildcard search
mdfind "kMDItemDisplayName=='*2024*'c"
```

### 7. Complex Queries

```bash
# PDFs in Documents modified this week containing "budget"
mdfind -onlyin ~/Documents "kMDItemContentTypeTree=='public.pdf' && kMDItemFSContentChangeDate>$time.this_week() && budget"

# Images larger than 5MB taken with iPhone
mdfind "kMDItemContentTypeTree=='public.image' && kMDItemAcquisitionMake=='Apple' && kMDItemFSSize>5242880"

# Documents authored by specific person, modified recently
mdfind "kMDItemContentTypeTree=='public.document' && kMDItemAuthors=='John Doe' && kMDItemFSContentChangeDate>$time.today(-30)"
```

### 8. Count Results

```bash
# Count PDFs
mdfind -count "kMDItemContentTypeTree=='public.pdf'"

# Count files modified today
mdfind -count "kMDItemFSContentChangeDate>$time.today()"
```

### 9. Integration with Other Tools

```bash
# Open all matching files
mdfind "kMDItemDisplayName=='*report*'c" | head -5 | xargs -I {} open "{}"

# Copy matching files
mdfind -onlyin ~/Documents "kMDItemContentTypeTree=='public.pdf'" -0 | xargs -0 -I {} cp "{}" ~/PDFs/

# Get file info
mdfind "kMDItemDisplayName=='*.jpg'c" | head -1 | xargs mdls

# Preview with Quick Look
mdfind "kMDItemDisplayName=='*screenshot*'c" | head -1 | xargs qlmanage -p
```

## Python Integration

When using from Python code:

```python
import subprocess

def spotlight_search(query: str, only_in: str = None, max_results: int = None) -> list[str]:
    """Search files using macOS Spotlight."""
    cmd = ["mdfind"]
    
    if only_in:
        cmd.extend(["-onlyin", only_in])
    
    cmd.append(query)
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    
    if result.returncode != 0:
        raise RuntimeError(f"mdfind failed: {result.stderr}")
    
    files = result.stdout.strip().split("\n")
    files = [f for f in files if f]  # Remove empty strings
    
    if max_results:
        files = files[:max_results]
    
    return files

# Usage examples
apps = spotlight_search("kMDItemKind=='Application'", max_results=10)
docs = spotlight_search("quarterly report", only_in="~/Documents")
recent = spotlight_search("kMDItemFSContentChangeDate>$time.today(-7)")
```

## Troubleshooting

### Spotlight Index Disabled?

```bash
# Check if indexing is enabled for a volume
mdutil -s /

# Rebuild index if needed
sudo mdutil -E /
```

### No Results?

1. Check if the directory is in Spotlight privacy exclusions (System Settings → Siri & Spotlight → Privacy)
2. Verify indexing is complete: `mdutil -t /`
3. Try with `-s` flag for raw query: `mdfind -s "kMDItemDisplayName=='file'"`

### Performance Tips

- Use `-onlyin` to limit search scope for faster results
- Use `-count` first to check result volume
- Use specific metadata attributes instead of generic text search
- For frequent searches, consider caching results

## Related Commands

| Command | Use Case |
|---------|----------|
| `find` | Traditional Unix search (slow, no content indexing) |
| `locate` | Database-based file search (updated periodically) |
| `mdls` | View metadata for a specific file |
| `mdutil` | Manage Spotlight indexing |
| `open` | Open files/URLs from command line |

## Security & Privacy

- Spotlight respects file permissions; users can only find files they can access
- Directories can be excluded from indexing via System Settings
- Encrypted volumes are typically not indexed
- Query history is not logged by the system
