# Feishu (Lark) Integration for Kimi Code CLI

This module provides integration with Feishu (Lark), allowing you to interact with Kimi Code CLI through Feishu messages on your phone.

## Features

- **🚀 Two Operation Modes**:
  - **SDK Mode** (Recommended): Long connection via WebSocket, no webhook URL needed
  - **Webhook Mode**: Traditional HTTP webhook (requires public URL)
- **Real-time Interaction**: Send tasks to Kimi Code CLI from your phone via Feishu
- **Live Tool Call Display**: See tool calls and their results in real-time
- **Streaming Updates**: Interactive cards update as the agent works
- **Multi-account Support**: Configure multiple Feishu bots
- **Access Control**: Restrict access to specific users or chats
- **WebSocket Gateway**: Monitor and control from other clients

## Quick Start (SDK Mode - Recommended)

### 1. Create a Feishu App

1. Go to [Feishu Open Platform](https://open.feishu.cn/app/)
2. Create a new app (Enterprise app)
3. Enable "Bot" capability
4. Get your **App ID** and **App Secret**

### 2. Configure Event Subscriptions

In your Feishu app settings:

1. Go to **Event Subscriptions**
2. Set **Subscription Mode** to **"Long connection"** (长连接)
3. Add these events:
   - `im.message.receive_v1` (Receive message)
   - `im.chat.member.bot.added_v1` (Bot added to chat)
   - `im.chat.access_event.bot_p2p_chat_entered_v1` (P2P chat entered)

### 3. Configure Permissions

Go to **Permissions & Scopes** and add:
- `im:chat:readonly` - Read chat information
- `im:message:send` - Send messages
- `im:message.group_msg` - Send group messages

### 4. Publish Your App

1. Go to **Version Management**
2. Create and publish a version
3. Get approval from your organization admin

### 5. Configure Kimi CLI

```bash
# Add your Feishu account
kimi feishu config --add mybot --app-id YOUR_APP_ID --app-secret YOUR_APP_SECRET

# Or use interactive mode
kimi feishu config --add mybot
```

### 6. Start the Server

```bash
# Start in SDK mode (default, recommended)
kimi feishu

# Or explicitly specify mode
kimi feishu --mode sdk

# With custom host/port
kimi feishu --host 0.0.0.0 --port 18888
```

## Alternative: Webhook Mode

If you prefer the traditional webhook approach:

### Setup

1. Configure the webhook URL in Feishu:
   ```
   http://your-server:18790/webhook/mybot
   ```
2. Use tunneling tools if running locally (ngrok, localtunnel, etc.)

### Start

```bash
kimi feishu --mode webhook
```

## Architecture

### SDK Mode (Recommended)
```
┌─────────────────┐     WebSocket      ┌──────────────────┐
│  Feishu App     │◄──────────────────►│  SDK Client      │
│  (Your Phone)   │   (Long Connection)│  (lark-oapi)     │
└─────────────────┘                    └────────┬─────────┘
                                                │
                      ┌─────────────────────────┘
                      │
                      ▼
              ┌──────────────────┐
              │  Gateway Server  │
              │  (ws://host:port)│
              └──────────────────┘
```

**Benefits:**
- ✅ No public webhook URL needed
- ✅ No tunnel/穿透 tools required
- ✅ More stable and secure
- ✅ Built-in encryption
- ✅ Automatic reconnection

### Webhook Mode
```
┌─────────────────┐     WebSocket      ┌──────────────────┐
│  Feishu App     │◄──────────────────►│  Gateway Server  │
│  (Your Phone)   │                    │  (ws://host:port)│
└────────┬────────┘                    └──────────────────┘
         │
         │ Webhook (HTTP)
         ▼
┌─────────────────┐
│  HTTP Server    │
│  (Port 18790)   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Message Handler│
│  + KimiSoul     │
└─────────────────┘
```

## Usage

### Send Messages

Simply send text messages to the bot in Feishu. The bot will:
1. Process your request using Kimi
2. Show tool calls in real-time
3. Display the final response in an interactive card

### Available Commands

- `/help` - Show help information
- `/reset` - Reset the conversation

### View Tool Calls

When the agent executes tools, you'll see:
- ⏳ Running tools with arguments
- ✅ Completed tools with results
- ❌ Failed tools with errors

## Configuration Options

```toml
# ~/.kimi/feishu.toml
host = "127.0.0.1"
port = 18789
mode = "sdk"  # "sdk" or "webhook"
default_account = "mybot"

[accounts.mybot]
app_id = "cli_xxxxx"
app_secret = "xxxxx"
# For webhook mode only:
# encrypt_key = "xxxxx"
# verification_token = "xxxxx"
allowed_users = []  # Restrict to specific user IDs
allowed_chats = []  # Restrict to specific chat IDs
auto_approve = false
show_tool_calls = true
show_thinking = true
```

## CLI Commands

### Configure Accounts

```bash
# List configured accounts
kimi feishu config --list

# Add a new account
kimi feishu config --add mybot --app-id cli_xxx --app-secret xxx

# Remove an account
kimi feishu config --remove mybot
```

### Check Status

```bash
kimi feishu status
```

### Interactive Setup Guide

```bash
kimi feishu setup
```

## Monitoring

### WebSocket Gateway

Connect to the gateway for real-time monitoring:

```javascript
const ws = new WebSocket('ws://localhost:18789');

ws.onopen = () => {
    ws.send(JSON.stringify({type: 'status', payload: {}}));
};

ws.onmessage = (event) => {
    console.log(JSON.parse(event.data));
};
```

## Security Considerations

1. **App Secret**: Keep your App Secret secure, never commit it to version control
2. **Access Control**: Use `allowed_users` and `allowed_chats` to restrict access
3. **Network**: SDK mode uses built-in encryption via WebSocket
4. **Webhook Mode**: Use HTTPS and signature verification in production

## Troubleshooting

### Bot Not Responding (SDK Mode)

1. Check if the server is running: `kimi feishu status`
2. Verify event subscription mode is set to "Long connection" in Feishu
3. Check server logs for connection errors
4. Ensure your app is published and approved

### Bot Not Responding (Webhook Mode)

1. Check if the server is running: `kimi feishu status`
2. Verify webhook URL is correct and accessible from Feishu
3. Check if tunnel (ngrok/localtunnel) is running
4. Verify encrypt_key if webhook verification is enabled

### Authentication Errors

1. Verify App ID and App Secret are correct
2. Check if the app is published in Feishu
3. Ensure the bot has necessary permissions

### Card Not Updating

1. Ensure the bot has permission to edit messages
2. Check for rate limiting
3. Verify `im:message:send` permission is granted

## Project Structure

```
feishu/
├── __init__.py       # Module exports
├── __main__.py       # Entry point for python -m
├── config.py         # Configuration management
├── client.py         # Legacy HTTP client (webhook mode)
├── sdk_client.py     # Official SDK client (sdk mode)
├── sdk_server.py     # SDK mode server with long connection
├── card.py           # Interactive card builder
├── message.py        # Message handler
├── gateway.py        # WebSocket gateway
├── server.py         # Webhook mode server
└── README.md         # This file
```

## References

- [Feishu Open Platform](https://open.feishu.cn/)
- [Feishu Python SDK (lark-oapi)](https://github.com/larksuite/oapi-sdk-python)
- [Long Connection Mode Documentation](https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case)
- [Feishu Bot API](https://open.feishu.cn/document/home/develop-a-bot-in-5-minutes/create-an-app)

## Migration from Webhook Mode

If you're currently using webhook mode and want to switch to SDK mode:

1. Update your Feishu app settings:
   - Change Event Subscription mode to "Long connection"
   - Remove the webhook URL (no longer needed)
   - Keep the same event subscriptions

2. Update your configuration:
   - Remove `encrypt_key` and `verification_token` (not needed in SDK mode)
   - Set `mode = "sdk"` in config (or leave as default)

3. Start the server:
   ```bash
   kimi feishu
   ```

No other changes needed! The bot will work the same way, just more reliably.
