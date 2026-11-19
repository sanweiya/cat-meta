# 配置与数据

[返回接手文档索引](README.md)

## 1. 数据目录

所有路径由 `catmeta/paths.py` 基于 XDG 环境变量计算。

| 内容 | 默认路径 |
| --- | --- |
| 设置 | `~/.config/cat-meta/settings.json`（0600） |
| 订阅索引 | `~/.config/cat-meta/profiles.json` |
| 订阅 YAML | `~/.local/share/cat-meta/profiles/<id>.yaml` |
| 运行时配置 | `~/.local/share/cat-meta/runtime/config.yaml`（0600） |
| mihomo 内核 | `~/.local/share/cat-meta/bin/mihomo` |
| Geo 数据 | `~/.local/share/cat-meta/geoip.metadb`、`geosite.dat`（或 `geoip.dat`） |
| 系统代理备份 | `~/.local/share/cat-meta/state/system-proxy.json` |
| 日志 | `~/.local/share/cat-meta/logs/app.log`、`core.log` |
| 自启动 | `~/.config/autostart/cat-meta.desktop` |
| 非 GNOME 桌面的代理环境文件 | `~/.config/environment.d/90-cat-meta-proxy.conf` |

`app.log` 每 1 MB 轮转、保留 2 份；`core.log` 每次启动内核时重新写入。`CATMETA_DEBUG=1` 会同时把日志输出到终端。

## 2. 设置

`catmeta/settings.py` 是线程安全的 JSON 存储，写入使用临时文件 + `os.replace`，权限 `0600`。未知键在加载时会被丢弃，所以新增设置必须先加到 `_defaults()`。

### 键分组

**内核**

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `core_path` | `""` | 自定义 mihomo 路径；为空时自动发现 |
| `api_port` | `19098` | 控制器端口，只监听 127.0.0.1 |
| `api_secret` | 随机生成 | Bearer secret，保存在 0600 设置文件中 |
| `mixed_port` | `7900` | 混合代理端口 |
| `mode` | `rule` | `rule` / `global` / `direct` |
| `log_level` | `info` | mihomo 日志级别 |
| `allow_lan` | `false` | 是否允许局域网连接 |
| `ipv6` | `false` | 是否启用 IPv6 |
| `tun` | `false` | TUN 模式 |
| `tun_stack` | `mixed` | TUN stack |
| `test_url` | gstatic 204 | 延迟测试地址 |

**界面与行为**

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `auto_start` | `false` | 开机自启动桌面文件 |
| `auto_start_core` | `false` | 启动应用时自动启动内核 |
| `start_minimized` | `false` | 启动时最小化到托盘 |
| `close_to_tray` | `true` | 关闭窗口时隐藏到托盘 |
| `stop_core_on_quit` | `true` | 退出时停止内核 |
| `show_tray` | `true` | 是否显示托盘 |
| `window_width` / `window_height` | `1200` / `800` | 外框尺寸 |
| `window_inset_width` / `window_inset_height` | `0` | CSD 外框与内容区差值 |
| `window_maximized` | `false` | 是否最大化 |
| `sidebar_width` | `200` | 侧栏宽度，160–420 |
| `last_page` | `home` | 上次页面 |
| `proxy_columns` | name/type/delay/status/actions | 代理页可见列 |

**订阅**

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `auto_update_profiles` | `true` | 是否自动更新远程订阅 |
| `use_builtin_editor` | `true` | 编辑 YAML 时优先用内置编辑器 |
| `update_interval_hours` | `24` | 自动更新间隔 |
| `last_profile_update_check` | `0` | 上次检查时间戳（内部用） |

**系统代理**

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `system_proxy` | `false` | 下次启动是否恢复系统代理 |

### 持久化语义

- 设置页保存走 `Controller.save_settings()`：校验端口、必要时重启内核、同步系统代理，并发出 `settings`/`mode` 事件。
- 窗口几何、侧栏宽度这类 **纯 UI 设置**直接 `settings.update()`，不触发 `settings` 事件，避免每拖一下都刷新设置页。
- 修改需要重启内核的键（端口、TUN、IPv6、日志级别、secret、core_path 等）时，`save_settings()` 会自动重启内核；其他键尽量热更新 `/configs`。

## 3. 运行时配置

`CoreManager.build_config_data()` 以订阅 YAML 为基础，覆盖/补全以下字段：

- `mixed-port`、`allow-lan`、`ipv6`
- `mode`、`log-level`
- `external-controller=127.0.0.1:<api_port>`、`secret`
- `profile.store-selected=true`、`store-fake-ip=true`（重启后保持节点选择）
- TUN 开启时：`tun.enable/stack/auto-route/auto-detect-interface/dns-hijack`，以及 DNS fake-ip 等默认值
- 没有任何代理组/节点时拒绝启动；没有规则时补 `MATCH,DIRECT`

生成后写入 `runtime/config.yaml`（0600），再用 `mihomo -t -d <APP_DATA> -f <config>` 校验，最后才启动进程。运行时配置是**生成物**，不要在仓库或用户数据里手改它来持久化设置。

## 4. 订阅

- 索引：`profiles.json`；内容：每个配置一个 `<id>.yaml`。
- 类型：`remote`（订阅链接）、`local`（导入的本地文件）、`manual`（手动创建/编辑）。
- 首次没有配置时安装“本地直连”示例（`profiles.SAMPLE_PROFILE`）。
- 导入支持 HTTP(S)、本地 YAML、手动粘贴；响应是 base64 时尝试解码。
- `ProfileStore.data()` 按 `(路径, mtime_ns, size)` 缓存解析结果，最多 8 份，避免切页重复解析大订阅。
- 远程更新会读取 `subscription-userinfo` 和 `profile-title` 响应头；`profile-title` 会覆盖显示名。
- 自动更新只处理 `remote` 且 `auto_update=True` 的配置；当前配置更新后会重启内核。

## 5. 系统代理

`SystemProxy` 有两种后端：

1. **GNOME 系**（Cinnamon/MATE/GNOME/Unity/Pantheon/Budgie）：
   - 写 `org.gnome.system.proxy`，HTTP/HTTPS/SOCKS 指向 `127.0.0.1:<mixed_port>`；
   - 立即生效；首次开启前备份到 `state/system-proxy.json`。
2. **其他桌面**（XFCE/LXQt/KDE…）：
   - 写 `~/.config/environment.d/90-cat-meta-proxy.conf`；
   - 同时推送到 D-Bus 激活环境和 systemd 用户环境；
   - 新启动的应用生效，重新登录后全面生效。

重要语义：

- `settings.system_proxy` 是“下次启动是否恢复”的偏好，不是实时状态。
- 退出应用时**一定**把实际系统代理关闭，避免代理指向已停止的内核；偏好保留。
- 应用启动后如果 `system_proxy` 偏好为 true，会先启动内核再重新应用代理。
- 订阅/内核下载不继承 shell 代理（`requests` 设置 `trust_env=False`），直连优先，失败后才尝试本机代理端口。

## 6. TUN 与 polkit

TUN 需要 mihomo 二进制带 `CAP_NET_ADMIN` + `CAP_NET_RAW`：

```bash
./scripts/setup-tun.sh
./scripts/setup-tun.sh /path/to/mihomo
```

也可以在“设置 → 内核 → 授权 TUN”里通过 `pkexec setcap` 完成。替换内核二进制后 capability 会丢失，需要重新授权。

可选的 polkit 规则 `/etc/polkit-1/rules.d/49-cat-meta-tun.rules`：

- 只放行 4 个 `org.freedesktop.resolve1` 的 DNS 相关动作；
- 只对当前本地用户生效；
- 用于避免 TUN 启动时反复弹出 systemd-resolved 的密码框；
- 删除该文件即可恢复系统默认授权行为。

## 7. 安全与隐私

- 控制器只监听 `127.0.0.1`，默认带随机 Bearer secret。
- 运行时配置、设置、订阅 YAML 均为 0600 临时文件 + 原子替换。
- 应用不收集遥测；订阅、节点、设置、日志只保存在本机。
- 仓库不包含任何订阅、节点、内核二进制或默认代理配置。
- 修改下载/订阅逻辑时，保持“显式直连/显式代理端口”的可预测行为，不要继承环境代理。
