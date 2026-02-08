#!/bin/bash
#
# OKbot 一站式安装脚本
# 飞书 × Kimi CLI 智能助手
#

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 打印函数
print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_step() { echo -e "\n${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; echo -e "${CYAN}  $1${NC}"; echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"; }

# 检查命令是否存在
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# 检查版本号是否满足要求
check_version() {
    local required="$1"
    local current="$2"
    
    # 提取主版本号
    local req_major=$(echo "$required" | cut -d. -f1)
    local cur_major=$(echo "$current" | cut -d. -f1)
    
    if [ "$cur_major" -ge "$req_major" ]; then
        return 0
    else
        return 1
    fi
}

# 欢迎界面
clear
echo -e "${CYAN}"
cat << 'EOF'
  ____  _  _     _       
 / __ \| || |   | |      
| |  | | || |_  | |_ ___ 
| |  | |__   _| | __/ _ \
| |__| |  | |   | || (_) |
 \____/   |_|    \__\___/ 
                          
EOF
echo -e "${NC}"
echo -e "${GREEN}Touch Kimi CLI anywhere, anytime.${NC}\n"
echo -e "🤖 ${CYAN}OKbot - Kimi Feishu Integration${NC}\n"
echo "本脚本将引导你完成 OKbot 的一站式安装"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 检查当前目录是否是 OKbot 项目
if [ ! -f "pyproject.toml" ] || [ ! -d "src/kimi_cli" ]; then
    print_error "请在 OKbot 项目目录中运行此脚本"
    echo ""
    echo "安装流程："
    echo "  1. git clone https://github.com/albertwyjoy-bit/OKbot.git"
    echo "  2. cd OKbot"
    echo "  3. ./install.sh"
    echo ""
    exit 1
fi

OKBOT_DIR="$(pwd)"
print_success "检测到 OKbot 项目目录: $OKBOT_DIR"

# ==================== 步骤 0: 环境检查 ====================
print_step "步骤 1/7: 环境检查"

# 检测操作系统
OS="unknown"
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macOS"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS="Linux"
fi
print_info "检测到操作系统: $OS"

# 检查 Python
if command_exists python3; then
    PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2)
    print_info "Python 版本: $PYTHON_VERSION"
    
    if ! check_version "3.12" "$PYTHON_VERSION"; then
        print_error "Python 版本需要 >= 3.12，当前版本: $PYTHON_VERSION"
        print_info "请先升级 Python: https://www.python.org/downloads/"
        exit 1
    fi
else
    print_error "未检测到 Python 3，请先安装 Python 3.12+"
    exit 1
fi

# 检查 Conda/Mamba
if command_exists conda; then
    CONDA_CMD="conda"
    print_success "检测到 Conda"
elif command_exists mamba; then
    CONDA_CMD="mamba"
    print_success "检测到 Mamba"
else
    print_warning "未检测到 Conda/Mamba"
    echo "Conda 用于管理 Python 虚拟环境，推荐安装"
    echo "安装地址: https://docs.conda.io/en/latest/miniconda.html"
    read -p "是否继续安装? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_info "安装已取消，请先安装 Conda 后重试"
        exit 1
    fi
    CONDA_CMD=""
fi

# 检查 Node.js
NODE_INSTALLED=false
if command_exists node; then
    NODE_VERSION=$(node --version | sed 's/v//')
    print_info "Node.js 版本: v$NODE_VERSION"
    
    if ! check_version "18" "$NODE_VERSION"; then
        print_warning "Node.js 版本需要 >= 18，当前版本: $NODE_VERSION"
        print_info "某些功能（如 Midscene）可能无法正常使用"
    else
        NODE_INSTALLED=true
    fi
else
    print_warning "未检测到 Node.js (>= 18 推荐用于 Midscene 功能)"
fi

# 检查 pnpm
if command_exists pnpm; then
    print_success "检测到 pnpm"
    PNPM_CMD="pnpm"
elif command_exists npm; then
    print_info "检测到 npm，将使用 npm"
    PNPM_CMD="npm"
else
    print_warning "未检测到 pnpm/npm"
    PNPM_CMD=""
fi

# 检查 Git
if ! command_exists git; then
    print_error "未检测到 Git，请先安装 Git"
    exit 1
fi
print_success "检测到 Git"

# ==================== 步骤 1: 创建 Conda 环境 ====================
print_step "步骤 2/7: 创建 Python 虚拟环境"

if [ -n "$CONDA_CMD" ]; then
    ENV_NAME="okbot"
    
    # 检查环境是否已存在
    if $CONDA_CMD env list | grep -q "^${ENV_NAME} "; then
        print_warning "Conda 环境 '$ENV_NAME' 已存在"
        read -p "是否删除并重建? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            $CONDA_CMD remove -n $ENV_NAME --all -y
            $CONDA_CMD create -n $ENV_NAME python=3.12 -y
        fi
    else
        $CONDA_CMD create -n $ENV_NAME python=3.12 -y
    fi
    
    print_success "Conda 环境 '$ENV_NAME' 已创建"
    print_info "激活环境: conda activate $ENV_NAME"
    
    # 激活环境并安装依赖
    eval "$($CONDA_CMD shell.bash hook)"
    $CONDA_CMD activate $ENV_NAME
else
    print_warning "未使用 Conda，将使用系统 Python"
    print_info "建议使用虚拟环境: python3 -m venv venv"
fi

# ==================== 步骤 3: 安装 Python 依赖 ====================
print_step "步骤 3/7: 安装 Python 依赖"

print_info "安装主程序依赖..."
pip install -e ".[dev]" -q

print_info "安装飞书 SDK..."
pip install lark-oapi -q

print_success "Python 依赖安装完成"

# ==================== 步骤 4: 安装 Node.js 依赖 ====================
if [ -n "$PNPM_CMD" ]; then
    print_step "步骤 4/7: 安装 Node.js 依赖"
    
    if [ -f "package.json" ]; then
        $PNPM_CMD install
        print_success "Node.js 依赖安装完成"
    else
        print_warning "未找到 package.json，跳过 Node.js 依赖安装"
    fi
else
    print_step "步骤 4/7: 跳过 Node.js 依赖安装"
    print_warning "未检测到包管理器，请手动运行: pnpm install 或 npm install"
fi

# ==================== 步骤 5: 配置智谱 API Key ====================
print_step "步骤 5/7: 配置智谱 AI API Key (可选)"

echo "智谱 AI API Key 用于："
echo "  - 语音消息识别 (ASR)"
echo "  - Midscene 图像理解"
echo ""
echo "申请地址: https://open.bigmodel.cn/"
echo ""

read -p "是否配置智谱 API Key? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    read -p "请输入智谱 API Key: " ZHIPU_API_KEY
    
    if [ -n "$ZHIPU_API_KEY" ]; then
        # 添加到 shell 配置文件
        SHELL_RC=""
        if [[ "$SHELL" == *"zsh"* ]]; then
            SHELL_RC="$HOME/.zshrc"
        elif [[ "$SHELL" == *"bash"* ]]; then
            SHELL_RC="$HOME/.bashrc"
        fi
        
        if [ -n "$SHELL_RC" ]; then
            echo "" >> "$SHELL_RC"
            echo "# OKbot 智谱 API Key" >> "$SHELL_RC"
            echo "export ZHIPU_API_KEY=\"$ZHIPU_API_KEY\"" >> "$SHELL_RC"
            print_success "API Key 已添加到 $SHELL_RC"
            print_info "请运行: source $SHELL_RC"
        fi
        
        # 立即导出
        export ZHIPU_API_KEY="$ZHIPU_API_KEY"
    fi
else
    print_info "跳过智谱 API Key 配置"
fi

# ==================== 步骤 6: 配置飞书应用 ====================
print_step "步骤 6/7: 配置飞书应用"

echo "飞书应用是 OKbot 与飞书通信的桥梁。"
echo ""
echo "配置步骤："
echo "  1. 访问 https://open.feishu.cn/app/ 并登录"
echo "  2. 点击「创建应用」→「企业自建应用」"
echo "  3. 在「凭证与基础信息」中获取 App ID 和 App Secret"
echo "  4. 添加「机器人」能力"
echo "  5. 在「权限管理」中添加必需的 API 权限"
echo "  6. 在「事件与回调」中配置事件订阅（长连接模式）"
echo "  7. 发布应用"
echo ""
echo "详细文档: https://github.com/albertwyjoy-bit/OKbot/blob/main/README.md"
echo ""

# 检查是否已有配置文件
KIMI_CONFIG_DIR="$HOME/.kimi"
FEISHU_CONFIG="$KIMI_CONFIG_DIR/feishu.toml"

if [ -f "$FEISHU_CONFIG" ]; then
    print_warning "检测到已有配置文件: $FEISHU_CONFIG"
    read -p "是否覆盖配置? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_info "保留现有配置，跳过此步骤"
        SKIP_FEISHU_CONFIG=true
    fi
fi

if [ -z "$SKIP_FEISHU_CONFIG" ]; then
    read -p "是否现在配置飞书应用凭证? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        read -p "请输入飞书 App ID (cli_xxxxx): " APP_ID
        read -p "请输入飞书 App Secret: " APP_SECRET
        
        # 创建配置目录
        mkdir -p "$KIMI_CONFIG_DIR"
        
        # 生成配置文件
        cat > "$FEISHU_CONFIG" << EOF
host = "127.0.0.1"
port = 18789
default_account = "bot"

[accounts.bot]
app_id = "$APP_ID"
app_secret = "$APP_SECRET"
show_tool_calls = true
show_thinking = true
auto_approve = true
EOF

        if [ -n "$ZHIPU_API_KEY" ]; then
            cat >> "$FEISHU_CONFIG" << EOF
asr_api_key = "$ZHIPU_API_KEY"
EOF
        fi

        print_success "配置文件已创建: $FEISHU_CONFIG"
    else
        print_info "跳过飞书配置，请稍后手动创建配置文件"
        print_info "模板位置: $OKBOT_DIR/feishu.example.toml"
    fi
fi

# ==================== 步骤 7: MCP 配置 ====================
print_step "步骤 7/7: MCP 服务器配置 (可选)"

echo "MCP 服务器扩展 OKbot 能力，支持："
echo "  - Chrome 浏览器控制"
echo "  - Android 设备控制"
echo "  - Notion 文档操作"
echo "  - 文件格式转换"
echo ""

MCP_CONFIG="$KIMI_CONFIG_DIR/mcp.json"

if [ -f "$MCP_CONFIG" ]; then
    print_warning "检测到已有 MCP 配置: $MCP_CONFIG"
    read -p "是否覆盖配置? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_info "保留现有配置，跳过此步骤"
        SKIP_MCP_CONFIG=true
    fi
fi

if [ -z "$SKIP_MCP_CONFIG" ]; then
    read -p "是否配置 MCP 服务器? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        
        # 获取智谱 API Key（如果需要配置 Midscene）
        if [ -z "$ZHIPU_API_KEY" ]; then
            read -p "是否输入智谱 API Key (用于 Midscene 图像理解)? (y/N): " -n 1 -r
            echo
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                read -p "请输入智谱 API Key: " ZHIPU_API_KEY
            fi
        fi
        
        # 询问是否配置 chrome-devtools
        read -p "是否配置 Chrome DevTools (浏览器调试)? (y/N): " -n 1 -r
        echo
        CONFIG_CHROME_DEVTOOLS=false
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            CONFIG_CHROME_DEVTOOLS=true
        fi
        
        # 询问是否配置 midscene-web
        read -p "是否配置 Midscene Web (浏览器自动化)? (y/N): " -n 1 -r
        echo
        CONFIG_MIDSCENE_WEB=false
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            if [ -z "$ZHIPU_API_KEY" ]; then
                print_warning "Midscene Web 需要智谱 API Key，请先配置"
            else
                CONFIG_MIDSCENE_WEB=true
            fi
        fi
        
        # 询问是否配置 midscene-android
        read -p "是否配置 Midscene Android (手机控制)? (y/N): " -n 1 -r
        echo
        CONFIG_MIDSCENE_ANDROID=false
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            if [ -z "$ZHIPU_API_KEY" ]; then
                print_warning "Midscene Android 需要智谱 API Key，请先配置"
            else
                CONFIG_MIDSCENE_ANDROID=true
                # 获取 Android SDK 路径
                DEFAULT_ANDROID_HOME="$HOME/Library/Android/sdk"
                if [ -d "$DEFAULT_ANDROID_HOME" ]; then
                    ANDROID_HOME="$DEFAULT_ANDROID_HOME"
                elif [ -n "$ANDROID_HOME" ]; then
                    ANDROID_HOME="$ANDROID_HOME"
                else
                    read -p "请输入 Android SDK 路径 [默认: $DEFAULT_ANDROID_HOME]: " ANDROID_HOME
                    ANDROID_HOME=${ANDROID_HOME:-$DEFAULT_ANDROID_HOME}
                fi
            fi
        fi
        
        # 询问是否配置 markitdown
        read -p "是否配置 Markitdown (文件格式转换)? (y/N): " -n 1 -r
        echo
        CONFIG_MARKITDOWN=false
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            CONFIG_MARKITDOWN=true
            print_info "提示: 需要先安装 markitdown-mcp: conda create -n markitdown python=3.12 -y && conda activate markitdown && pip install markitdown-mcp"
        fi
        
        # 询问是否配置 notion
        read -p "是否配置 Notion (文档操作)? (y/N): " -n 1 -r
        echo
        CONFIG_NOTION=false
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            CONFIG_NOTION=true
            read -p "请输入 Notion Integration Token: " NOTION_TOKEN
        fi
        
        # 构建 MCP 配置
        MCP_CONTENT='{
  "mcpServers": {'
        
        FIRST=true
        
        if [ "$CONFIG_CHROME_DEVTOOLS" = true ]; then
            if [ "$FIRST" = false ]; then
                MCP_CONTENT="$MCP_CONTENT,"
            fi
            FIRST=false
            MCP_CONTENT="$MCP_CONTENT
    \"chrome-devtools\": {
      \"command\": \"npx\",
      \"args\": [\"-y\", \"chrome-devtools-mcp@latest\"]
    }"
        fi
        
        if [ "$CONFIG_MIDSCENE_WEB" = true ]; then
            if [ "$FIRST" = false ]; then
                MCP_CONTENT="$MCP_CONTENT,"
            fi
            FIRST=false
            
            MCP_CONTENT="$MCP_CONTENT
    \"midscene-web\": {
      \"command\": \"npx\",
      \"args\": [\"-y\", \"@midscene/web-bridge-mcp\"],
      \"env\": {
        \"MIDSCENE_MODEL_BASE_URL\": \"https://open.bigmodel.cn/api/paas/v4\",
        \"MIDSCENE_MODEL_API_KEY\": \"$ZHIPU_API_KEY\",
        \"MIDSCENE_MODEL_NAME\": \"glm-4v-plus\",
        \"MIDSCENE_MODEL_FAMILY\": \"glm-v\",
        \"MCP_SERVER_REQUEST_TIMEOUT\": \"600000\"
      }
    }"
        fi
        
        if [ "$CONFIG_MIDSCENE_ANDROID" = true ]; then
            if [ "$FIRST" = false ]; then
                MCP_CONTENT="$MCP_CONTENT,"
            fi
            FIRST=false
            
            MCP_CONTENT="$MCP_CONTENT
    \"midscene-android\": {
      \"command\": \"npx\",
      \"args\": [\"-y\", \"@midscene/android-mcp\"],
      \"env\": {
        \"MIDSCENE_MODEL_BASE_URL\": \"https://open.bigmodel.cn/api/paas/v4\",
        \"MIDSCENE_MODEL_API_KEY\": \"$ZHIPU_API_KEY\",
        \"MIDSCENE_MODEL_NAME\": \"glm-4v-plus\",
        \"MIDSCENE_MODEL_FAMILY\": \"glm-v\",
        \"MCP_SERVER_REQUEST_TIMEOUT\": \"800000\",
        \"ANDROID_HOME\": \"$ANDROID_HOME\",
        \"PATH\": \"$ANDROID_HOME/platform-tools:/usr/local/bin:/usr/bin:/bin\"
      }
    }"
        fi
        
        if [ "$CONFIG_MARKITDOWN" = true ]; then
            if [ "$FIRST" = false ]; then
                MCP_CONTENT="$MCP_CONTENT,"
            fi
            FIRST=false
            
            MCP_CONTENT="$MCP_CONTENT
    \"markitdown\": {
      \"command\": \"python\",
      \"args\": [\"-m\", \"markitdown_mcp\"],
      \"env\": {
        \"PATH\": \"$HOME/.conda/envs/markitdown/bin:/usr/local/bin:/usr/bin:/bin\"
      }
    }"
        fi
        
        if [ "$CONFIG_NOTION" = true ]; then
            if [ "$FIRST" = false ]; then
                MCP_CONTENT="$MCP_CONTENT,"
            fi
            FIRST=false
            
            MCP_CONTENT="$MCP_CONTENT
    \"notion\": {
      \"command\": \"npx\",
      \"args\": [\"-y\", \"@notionhq/notion-mcp-server\"],
      \"env\": {
        \"NOTION_API_TOKEN\": \"$NOTION_TOKEN\",
        \"NOTION_VERSION\": \"2025-09-03\"
      }
    }"
        fi
        
        MCP_CONTENT="$MCP_CONTENT
  }
}"

        echo "$MCP_CONTENT" > "$MCP_CONFIG"
        print_success "MCP 配置已创建: $MCP_CONFIG"
        
        # 显示后续安装提示
        if [ "$CONFIG_MARKITDOWN" = true ]; then
            echo ""
            print_info "Markitdown 安装提示:"
            echo "  conda create -n markitdown python=3.12 -y"
            echo "  conda activate markitdown"
            echo "  pip install markitdown-mcp"
        fi
    else
        print_info "跳过 MCP 配置"
    fi
fi

# ==================== 完成 ====================
print_step "🎉 安装完成!"

echo -e "${GREEN}OKbot 安装成功！${NC}\n"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📁 安装目录: $OKBOT_DIR"
echo "⚙️  配置文件: $KIMI_CONFIG_DIR/"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "🚀 启动方式:"
echo "   1. 进入目录: cd $OKBOT_DIR"
if [ -n "$CONDA_CMD" ]; then
    echo "   2. 激活环境: conda activate okbot"
fi
echo "   3. 启动服务: python -m kimi_cli.feishu"
echo ""
echo "💡 首次启动:"
echo "   - 会显示 Kimi OAuth 登录链接"
echo "   - 请在浏览器中完成授权"
echo ""
echo "📚 常用命令:"
echo "   /help      - 显示帮助"
echo "   /yolo      - 切换授权模式"
echo "   /sessions  - 查看可用 sessions"
echo "   /mcp       - 查看 MCP 状态"
echo ""
echo "📖 详细文档: https://github.com/albertwyjoy-bit/OKbot"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 创建启动脚本快捷方式
# 询问是否立即启动
read -p "是否立即启动 OKbot? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    cd "$OKBOT_DIR"
    if [ -n "$CONDA_CMD" ]; then
        eval "$($CONDA_CMD shell.bash hook)"
        $CONDA_CMD activate okbot
    fi
    python -m kimi_cli.feishu
fi
