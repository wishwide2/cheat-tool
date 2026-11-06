# 阿卡桑冒险工坊

![阿卡桑冒险工坊图标](ui_icon.png)

适用于《赤ずきんと不思議な洞窟》9.51 的 Windows 存档编辑器。打开自己的存档，预览修改，再生成一个新槽位供游戏读取。

## 功能

- 调整主角污染值（0–200）、角色能力、资源、物品、装备和技能。
- 批量调整物品数量、应用角色能力方案、查看变更，以及从备份生成新存档。
- 从工坊启动本机游戏。玩法规则修改和本地语言切换需要额外的本地工具或资源。

## 从源码运行

需要 Windows 和带 Tkinter 的 Python 3.13。在项目目录打开 PowerShell，执行以下命令。

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe .\akasan_editor.py
```

1. 在游戏内先保存一次，打开工坊的「打开存档」，选择本地 `Save` 目录中的 `.sav`。
2. 在「角色成长」修改污染值，或进入其他页面调整资源。物品名称来自当前存档，缺少名称时显示编号。
3. 通过「修改预览」核对修改，再点击「生成新存档」，选择 `Save` 目录中的空槽位。回到游戏读取对应槽位。
4. 想从工坊启动时，在「启动游戏」页选择 `Game.exe` 所在的目录。普通启动使用该目录里已安装的资源。

工坊将设置保存到 `%LOCALAPPDATA%\AkasanWorkshopPublic`。生成新存档时，工坊会在原存档所在目录保留一份备份。

## 可选的玩法和语言功能

玩法规则修改需要本地游戏的 `Data\BasicData.wolf`，以及支持 `-p 3` 打包的兼容 UberWolf 命令行工具。将 `UberWolfCli.exe` 放入工坊旁的 `tools` 目录，或在启动工坊前设置 `AKASAN_ARCHIVER` 为其完整路径。若打包工具无法使用含非 ASCII 字符的临时目录，可以将 `AKASAN_TEMP` 设置到可写的 ASCII 路径。[UberWolf 上游](https://github.com/Sinflower/UberWolf)

应用或恢复玩法设置前先退出游戏。工坊为所选游戏目录保存原始归档，可从「玩法设置」恢复。

游戏语言选项取决于本机游戏目录中可用的兼容 `Localization` 资源。切换需要该资源清单所指定的命令行工具版本。存档编辑和普通启动只需要本地存档或游戏。

## 在本地打包

创建虚拟环境后，在项目目录执行以下命令。生成的程序位于 `dist\AkasanWorkshop.exe`。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm .\AkasanWorkshop.spec
```

## 许可

本项目的自有源码和工坊图标采用 MIT 许可证。原游戏由 マインドトラップ（邪心）制作。游戏程序、素材、剧情及翻译资源、存档与可选归档工具由使用者在本机自行提供。[MIT 许可证](LICENSE) · [第三方说明](THIRD_PARTY_NOTICES.md)
