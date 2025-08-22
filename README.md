# 项目简介
闲的没事，想着把之前nas开公益emby解码过热的问题解决了，结果发现emby的硬件解码功能是高级版功能，真是难评.....于是毅然决然转Jellyfin！然后从网上折腾自动化的时候觉得网上的代码都很粗糙，于是写了这个代码.....
# 使用方法
一.源码部署
首先安装Python3，命令如下：
```shell
apt install python3 pip -y
```
等待安装完成后下载Python文件
```shell
mkdir jb && cd jb && wget https://raw.githubusercontent.com/mchim8623/Telegram-Jellyfin-bot-plus/refs/heads/main/bot.py
```
安装依赖(没开虚拟环境）
```python
 pip install asyncio logging secrets string random aiosqlite requests python-telegram-bot nest_asyncio --break-system-packages
```
安装依赖(在虚拟环境）
```python
 pip install asyncio logging secrets string random aiosqlite requests python-telegram-bot
```
开启机器人
```python
python3 bot.py
```
开启后台服务
```shell
[Unit]
Description=tgbot
After=network.target
[Service]
ExecStart=python3 /你服务器的工作目录/1.py
Restart=always
[Install]
WantedBy=multi-user.target
```
自启动服务
```shell
systemctl start /你的工作目录/你自定义的文件名.service
systemctl enable 你自定义的文件名.service
```
# 常见问题
1.没有邀请链接？
请检查注册成功信息。
2.没法添加货币吗？
暂时没写 后续可能调整
3.邀请成功后没有添加货币？
目前未复现bug 如果复现请提issues
# 感谢项目
https://github.com/Prejudice-Studio/Telegram-Jellyfin-Bot
# 目前已知bugs
管理员指令基本无效（除了/toggle_registration）
# 联系开发者
Whatsapp：+852 60442173
telegram：https://t.me/Love_benghuai3
