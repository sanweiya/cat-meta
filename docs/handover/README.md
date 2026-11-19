# Cat Meta 接手文档

面向后续维护者。用户侧功能、安装与免责声明见 [项目 README](../../README.md)。

Cat Meta 是一个为 Linux Mint 打造的原生 **mihomo（Clash Meta）** 图形客户端：Python 3 + GTK 3 / PyGObject + XApp，不依赖 Electron，不内置内核、订阅或节点。

- 当前版本：`1.0.0`（`catmeta/__init__.py`）
- 应用 ID：`com.github.catmeta.CatMeta`
- 许可证：GPL-3.0（仅覆盖本仓库代码）
- 分支：`master`；当前无 CI 配置、无 Git tag
- 最近相关改动：`fd541f7` 固定并允许拖拽侧栏宽度

## 5 分钟启动

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 python3-requests python3-psutil \
                 gir1.2-xapp-1.0 libcap2-bin

python3 run.py                  # 启动
CATMETA_DEBUG=1 python3 run.py  # 终端同时输出 debug 日志
```

首次启动会创建数据目录，并在没有配置时安装“本地直连”示例；还需要在“设置 → 内核”下载或选择 mihomo。

安装：

```bash
./scripts/install.sh            # 用户级：~/.local/lib/cat-meta + ~/.local/bin/cat-meta
./scripts/install.sh --system   # 系统级：/usr/lib/cat-meta + /usr/bin/cat-meta
```

测试使用标准库 `unittest`，项目不依赖 pytest：

```bash
python3 -m unittest discover -s tests -v
```

> 注意：当前有一个既有测试会失败，并可能实际改写当前用户的系统代理。运行全量测试前先看 [开发与发布](development.md#3-测试) 中的说明。

## 文档地图

| 文档 | 内容 | 适合什么时候读 |
| --- | --- | --- |
| [架构与生命周期](architecture.md) | 入口、启动/关闭顺序、Controller 事件总线、线程模型、内核进程、托盘/单实例 | 先理解程序怎么跑起来 |
| [配置与数据](configuration.md) | 设置键、订阅存储、运行时配置、数据目录、系统代理、TUN/polkit | 改配置、排查数据/代理问题 |
| [界面与扩展](ui.md) | MainWindow、页面、侧栏宽度、主题/字体、加页面/设置/API/托盘项 | 改 UI 或加功能 |
| [开发与发布](development.md) | 调试、测试、手工验收、已知坑、版本/发布、仓库约定 | 日常开发与提交前检查 |
| [AGENTS.md](../../AGENTS.md) | 推送/上传到外网的授权规则 | 任何 push 前必读 |

建议阅读顺序：本页 → `architecture.md` → `configuration.md` → `ui.md` → `development.md`。

## 接手第一天清单

- [ ] 安装依赖并 `python3 run.py` 启动成功。
- [ ] 在“设置 → 内核”下载/选择 mihomo，启动一次内核。
- [ ] 导入一个测试订阅或使用示例配置，浏览代理/连接/规则/日志页。
- [ ] 跑单元测试；先确认/隔离 `development.md` 中提到的既有测试副作用。
- [ ] 按 `development.md` 做一次完整冒烟（侧栏拖拽、托盘、系统代理退出行为）。
- [ ] 读 `controller.py`、`mihomo.py`、`window.py`、`sidebar.py`，这四个文件覆盖主要复杂度。

## 仓库约定

- 提交信息沿用英文祈使句，例如 `Add a fixed, resizable sidebar width`。
- 不提交用户数据、订阅 YAML、内核二进制、日志；`.gitignore` 已覆盖常见项。
- **未经用户明确、单次授权，不得将任何代码/数据推送到云端或外网仓库**，详见 [AGENTS.md](../../AGENTS.md)。
