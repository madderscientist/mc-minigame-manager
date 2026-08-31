$ErrorActionPreference = "Stop"
$Distribution = "Ubuntu"

# 任务计划程序应设置为“计算机启动时”、使用注册该发行版的 Windows 用户、无需登录运行。
wsl.exe -d $Distribution --exec /usr/bin/systemctl start mc-manager.target
wsl.exe -d $Distribution --exec /usr/bin/systemctl is-active --quiet mc-manager.target
