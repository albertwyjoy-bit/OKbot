# OKbot 语音消息处理

OKbot 支持接收飞书语音消息，使用 **智谱 GLM-ASR-2512** 进行语音识别，并将识别结果作为文本输入进行处理。

## 功能特性

- 🎤 支持飞书语音/语音消息
- 📝 使用 GLM-ASR-2512 进行语音识别
- 🇨🇳 中文识别效果优秀，支持方言
- 🔢 精准识别数字与单位组合
- 🗣️ 智能解析不连贯语句

## GLM-ASR-2512 介绍

GLM-ASR-2512 是智谱新一代语音识别模型，特点：

- 字符错误率（CER）仅 0.0717
- 支持中英文语境自动切换
- 支持方言识别（天津话、粤语等）
- 支持低音量语音处理
- 抗噪音干扰能力强
- 智能解析重复、卡顿语句

官方文档：https://docs.bigmodel.cn/cn/guide/models/sound-and-video/glm-asr-2512

## 使用方法

### 1. 获取智谱 API Key

1. 访问 [智谱 AI 开放平台](https://open.bigmodel.cn/)
2. 注册/登录账号
3. 在"API Keys"页面创建新的 API Key
4. 复制 API Key 用于配置

### 2. 配置环境变量

在启动 OKbot 之前，设置环境变量：

```bash
export ZHIPU_API_KEY="your-zhipu-api-key"
```

或者在配置文件中设置（不推荐，安全性较低）：

```toml
[accounts.bot]
app_id = "cli_xxxxxxxx"
app_secret = "xxxxxxxx"
asr_api_key = "your-zhipu-api-key"
```

### 3. 在飞书中发送语音消息

在飞书对话中，点击输入框旁边的麦克风图标，按住说话即可。

OKbot 会自动：
1. 下载语音文件
2. 使用 GLM-ASR-2512 进行语音识别
3. 将识别结果发送给你确认
4. 基于识别内容进行处理和回复

## 效果示例

用户发送语音 → Bot 回复：

```
🎤 收到语音消息
正在下载并识别...

📝 正在进行语音识别 (GLM-ASR-2512)...

✅ 语音识别完成！
🎯 识别结果：打开我的邮箱，看看里面有什么新邮件

[Bot 执行相应操作...]
```

## 故障排查

### 语音消息无法识别

1. **检查 ZHIPU_API_KEY 是否设置**
   ```bash
   echo $ZHIPU_API_KEY
   ```

2. **检查 API Key 是否有效**
   测试 API Key：
   ```bash
   curl --request POST \
       --url https://open.bigmodel.cn/api/paas/v4/audio/transcriptions \
       --header 'Authorization: Bearer YOUR_API_KEY' \
       --header 'Content-Type: multipart/form-data' \
       --form model=glm-asr-2512 \
       --form stream=false \
       --form file=@test-audio.mp3
   ```

3. **查看日志**
   运行 OKbot 时添加 `--verbose` 参数查看详细日志：
   ```bash
   kimi feishu --verbose
   ```

### 识别准确率低

- GLM-ASR-2512 对中文支持较好，但如果识别效果不佳：
  - 确保语音清晰，背景噪音不要过大
  - 语速适中，不要过快
  - 避免多人同时说话

## 技术实现

语音消息处理流程：

```
飞书语音消息
    ↓
sdk_server.py (handle_message_event)
    ↓
检测 msg_type == "audio"
    ↓
sdk_client.download_audio() 下载语音文件
    ↓
asr.py (GLMASR2512.transcribe()) 语音识别
    ↓
智谱 API: POST https://open.bigmodel.cn/api/paas/v4/audio/transcriptions
    ↓
将识别结果作为 text 输入
    ↓
KimiSoul 处理 → 回复
```

相关代码：
- `src/kimi_cli/feishu/sdk_server.py` - 消息处理
- `src/kimi_cli/feishu/sdk_client.py` - 音频下载
- `src/kimi_cli/utils/asr.py` - GLM-ASR-2512 语音识别

## 安全与隐私

- 语音文件临时保存在本地工作目录
- 使用 GLM-ASR-2512 时，语音数据会发送到智谱 AI 服务器
- 请勿在语音中发送敏感信息
- API Key 建议通过环境变量设置，避免泄露

## 参考资料

- [GLM-ASR-2512 官方文档](https://docs.bigmodel.cn/cn/guide/models/sound-and-video/glm-asr-2512)
- [智谱 AI 开放平台](https://open.bigmodel.cn/)
- [智谱 API 错误码](https://docs.bigmodel.cn/cn/guide/development/error-code)
