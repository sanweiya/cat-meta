# 开发与发布

[返回接手文档索引](README.md)

## 1. 环境与命令

依赖安装见 [接手文档首页](README.md#5-分钟启动) 和项目 [README](../../README.md)。常用命令：

```bash
python3 run.py
CATMETA_DEBUG=1 python3 run.py
python3 -m unittest discover -s tests -v
./scripts/install.sh
./scripts/install.sh --system
```

- 项目没有 pytest/pyproject 配置，测试是标准库 `unittest`。
- 应用日志：`~/.local/share/cat-meta/logs/app.log`（1 MB 轮转）。
- 内核日志：`~/.local/share/cat-meta/logs/core.log`。
- 设置中的 `api_secret` 用于本地控制器 API。

## 2. 调试

```bash
tail -f ~/.local/share/cat-meta/logs/app.log
tail -f ~/.local/share/cat-meta/logs/core.log

curl -H "Authorization: Bearer <secret>" http://127.0.0.1:19098/version
curl --proxy http://127.0.0.1:7900 https://www.gstatic.com/generate_204 -I
```

常用排查点：

- 内核起不来：先看 `core.log` 尾部和“配置校验失败”；再检查端口占用、TUN capability、Geo 数据。
- 系统代理异常：确认桌面后端（GSettings vs env 文件）和 `settings.system_proxy` 偏好；退出应用应把实际代理关闭。
- 托盘消失：面板是否有 XEmbed/StatusNotifier 宿主；XFCE 需要“通知区域”插件。
- 进程名显示 `python3`：确认启动的是当前 `run.py`；它会用 `prctl(PR_SET_NAME)` 把 comm 改为 `cat-meta`，旧进程重启后才生效。
- UI 卡顿：检查是否有网络/解析工作跑到主线程；用 `run_async()` 和页面 `on_show()` 懒加载。

## 3. 测试

### 命令与覆盖

```bash
python3 -m unittest discover -s tests -v
```

现有测试覆盖：

- 配置生成、TUN/端口覆盖、内核进程识别
- 订阅导入/更新/缓存、订阅头解析
- 设置持久化
- Geo 数据检测
- GSettings 安全访问
- polkit 规则文本与检测
- 系统代理后端选择
- 格式化/延迟分类
- 代理页操作列命中区域

### 已知测试问题（重要）

`tests/test_system_proxy.py::EnvProxyBackendTests::test_apply_writes_env_file_and_publishes` 在当前 **Cinnamon** 环境会失败，并且有副作用：

- `_proxy()` 里的 `XDG_CURRENT_DESKTOP=XFCE` mock 只包住了 `proxy.backend` 断言，没有包住后面的 `proxy.apply()`；
- 因此 `apply()` 在真实 Cinnamon 环境走了 gsettings 后端，测试不仅找不到临时 env 文件，还会**真的修改当前用户的系统代理**（指向 `127.0.0.1:7900`）。

修测试时，把 `apply()`（以及 `disable()` 等调用）放进同一个 `mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "XFCE"})` 上下文，或把 backend 显式固定为 env。修复前不要在有真实代理会话时直接跑全量测试。

## 4. 手工冒烟清单

1. 首次启动：没有配置时出现“本地直连”示例；设置页能下载/选择内核。
2. 启动内核：概览状态变为已连接，流量/连接数/内存更新；代理页能看到分组和节点。
3. 系统代理：Cinnamon 下切换后 `gsettings get org.gnome.system.proxy mode` 应为 `manual`；退出后应为 `none`。
4. 托盘：有宿主时左键显示窗口、右键原生菜单；无宿主时不会把窗口藏到找不到。
5. 订阅：导入远程/本地/手动配置；更新后当前配置自动重启。
6. 侧栏：长网速文本不改变宽度；拖拽调节、双击复位、重启保留；内容区按钮可点击；工具栏横线无缝。
7. 连接/规则/日志：搜索、右键菜单、暂停、导出在窄窗口下可用。
8. TUN：授权后在支持环境启动，检查 DNS/路由；替换内核后重新授权。
9. 退出：系统代理实际关闭，内核按 `stop_core_on_quit` 停止，无残留 mihomo。
10. 进程名：新实例在系统监视器中显示 `cat-meta`（`ps -o comm` 一致）。

## 5. 发布

1. 更新版本：`catmeta/__init__.py::VERSION`。
2. 跑全量测试（注意上一节的副作用）+ 手工冒烟。
3. UI 有可见变化时更新 `docs/screenshots/` 与 README 截图。
4. 用 `scripts/install.sh` 做一次用户级/系统级安装验证（桌面文件、图标、启动器、MIME 关联）。
5. 当前没有 CI、没有 Git tag；发布是手工步骤，如需 tag/Release 由维护者决定。

## 6. 仓库与协作约定

- 分支：`master`，当前跟踪 `origin/master`。
- 提交信息：英文祈使句，例如 `Add a fixed, resizable sidebar width`。
- 不提交用户数据、订阅 YAML、内核二进制、日志；`.gitignore` 已覆盖常见项。
- **推送策略**：见 [AGENTS.md](../../AGENTS.md)。未经用户明确、**单次**授权，不得将任何代码/数据推送到云端或外网仓库；以前推送过不代表这次也授权。需要推送时先说明目标仓库、分支和内容。

## 7. 跨模块常见坑

1. **GSettings 不能直接 `Gio.Settings.new()`**：schema 缺失时 GLib 会 abort；统一用 `catmeta/gsettings.py::new()`。
2. **下载/订阅不要继承 shell 代理**：`net.py` 使用 `trust_env=False`，直连优先，失败后才尝试本机代理端口。
3. **残留内核只按本应用配置识别**：`CoreManager._looks_like_core()` 匹配 `-f runtime/config.yaml` 和 `-d APP_DATA`，不要放宽匹配，否则可能杀别的 mihomo。
4. **窗口几何保存外框尺寸**：`window_width/height` 是外框，`get_size()` 是内容尺寸，用 `window_inset_*` 换算；不要混用。
5. **系统代理偏好 ≠ 当前状态**：退出一定关闭实际代理，但保留 `settings.system_proxy` 供下次恢复。
6. **TUN capability 会因替换内核丢失**；`getcap` 检查不到时不要误判为已授权。
7. **CSD/主题类名**：不要用通用 `.error`/`.success` 做 toast；现有代码用 `toast-error`/`toast-success`，避免继承 Greybird 消息框样式。
8. **侧栏热区**：必须是骑跨描边的小窗口 + pass-through 覆盖层；改回铺满内容区的 overlay 会导致光标全屏 resize、按钮点不动，详见 [界面与扩展](ui.md#侧栏常见坑)。
9. **进程名**：`run.py` 只改 `comm`，`ps args`/`/proc/exe` 仍是 python3；这是正常的，不要把“System Monitor 显示 cat-meta、ps args 显示 python3”当成回归。
