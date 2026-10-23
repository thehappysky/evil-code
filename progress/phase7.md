# Phase 7：沙箱体系（Sandbox v1）

> 最后更新: 2026-08-05
> 设计文档: `design-docs/沙箱方案-v1.md`
> 参考实现: `D:\Pycharm_py\gork_build\grok-build`（Rust, nono/bwrap 思路平移）

---

## 阶段总览

| 项 | 内容 | 状态 |
|:---|:---|:---|
| 1 | 路由分类器（local / clean / net / work + 生命周期 + risk_reason） | ✅ 完成 |
| 2 | 出站白名单过滤代理（CONNECT + HTTP absolute-URI） | ✅ 完成 |
| 3 | 项目级沙箱管理器（每项目 work 卷、临时容器、strict/auto/off） | ✅ 完成 |
| 4 | 权限联动（沙箱可用自动放行 / 不可用回退审批带原因链） | ✅ 完成 |
| 5 | TUI 状态栏 `sandbox:auto` + 工具卡片原因展示 | ✅ 完成 |
| 6 | 生命周期 GC（定时/新建前/手动；重视度驱逐 + protect） | ✅ 完成 |
| 7 | CLI 管理命令 `code-agent sandbox list/gc/protect/remove/clean-cache` | ✅ 完成 |
| 8 | 子代理接入沙箱（不可静默回退） | ✅ 完成 |
| 9 | v2 小项：TUI `/sandbox`、sandbox.toml、真实卷大小驱逐 | ✅ 完成 |
| 10 | 真机冒烟（Docker 28.5.2 + 镜像加速源） | ✅ 完成 |
| 11 | Draft 工作副本（git worktree，CLI + TUI + 冒烟） | ✅ 完成 |
| 12 | 自定义 profile（extends / read_only / read_write / deny / restrict_network） | ✅ 完成 |
| 13 | net 容器复用（docker exec 会话容器） | ✅ 完成 |
| 14 | 精细化挂载（/scratch tmpfs + profile 挂载） | ✅ 完成 |

---

## 已实施内容（2026-08-05）

- `sandbox/policy.py`：分类器。`SCRIPT_EXECUTORS` 优先（脚本一律沙箱）；
  包安装器 → work；curl/wget → net；git 写子命令 → work；敏感路径参数 → clean；
  白名单安全命令 → local；其余 → clean。临时目录 → ephemeral。
- `sandbox/proxy.py`：`EgressProxy` 白名单代理，默认放行 pypi/github/npm 等域名，
  非白名单 403；`_pump_both` 双向泵任一端结束即关闭另一端。
- `sandbox/manager.py`：`SandboxManager`。每项目 `code-agent-<id>-work` 命名卷
  （挂 /env，pip/npm 缓存与 site-packages 落卷）；容器一律 `docker run --rm`；
  work 容器内 root（隔离仍成立）；strict 强制全部沙箱；Docker 不可用回退本机
  并标记 `fallback`（须已显式审批）。GC：退出容器 / 悬空 ephemeral 卷 /
  `max_projects` 上限 / `docker system df` 水位驱逐；重视度 = 7 天窗口访问频率。
- `sandbox/state.py`：项目状态 JSON（last_used / access_count / protected），
  原子写入 `~/.code-agent/sandbox/state.json`。
- `permission/manager.py`：`sandbox_mode` + `configure_sandbox()`；sandbox 可用 →
  ALLOW（`sandbox_auto:<tier>:<reason>`）；不可用 → headless DENY / 交互 ASK，
  `last_reason` 供 TUI 展示原因链。
- `cli/main.py`：`_async_main` 创建共享 SandboxManager；`_build_tools` 注册
  `shell_exec_routed`；TUI 定时 GC 任务；`code-agent sandbox ...` 子命令。
- `tui/tui.py`：runtime-bar 新增 `sandbox:auto/off` 指示；`adapters.py` 把
  `last_reason` 带入权限事件与审批弹窗。
- `tools/result.py`：`ToolResult.meta` 携带执行模式/档位/生命周期。
- 子代理：`_build_subagent_factory` 传入共享 SandboxManager，
  `allow_sandbox_fallback=False`（Docker 不可用时直接失败，不静默回退）。
- TUI `/sandbox status|list|gc|clean-cache|protect|unprotect|remove`。
- `sandbox/config.py`：`~/.code-agent/sandbox.toml` + 项目配置（追加式），
  支持 `image` / `egress_extra` / `max_projects` / `disk_warning_gb`；
  代理监听 0.0.0.0、容器经 host.docker.internal 访问。
- 真实卷大小驱逐：`docker system df -v` 读取受管卷大小，失败回退估算。
- `sandbox/draft.py`：DraftManager（git worktree create/status/merge/discard），
  记录持久化 `~/.code-agent/sandbox/drafts.json`；merge 用 squash 提交、
  冲突自动中止；discard 清理 worktree/分支/沙箱卷。
- CLI `code-agent draft create|list|status|merge|discard`；
  TUI `/draft create|list|status|merge|discard`。
- `sandbox/config.py`：`[sandbox.profiles.<name>]`（extends / read_only /
  read_write / deny / restrict_network），全局优先、项目追加。
- `manager.py`：profile 额外挂载 `/mnt/ro-i`、`/mnt/rw-i`；deny 用空占位
  绑定覆盖工作区内路径；restrict_network=true 强制 `--network none`；
  所有档位挂 `/scratch` tmpfs（TMPDIR=/scratch）；net 档会话级复用容器
  （docker exec + timeout 包裹），工作目录变化或启用 profile 时回退 docker run。
- CLI `--sandbox-profile` + `CODE_AGENT_SANDBOX_PROFILE`；status 显示 profile。

## 测试

`tests/test_sandbox/`（policy 14 + proxy 4 + manager 11 + config 3）、
`tests/test_permission/test_sandbox_auto_allow.py`（5）、TUI `/sandbox`（2）：
共 39 个沙箱相关用例全部通过。全量回归 **1026 passed / 70.72%**（2026-08-06，
短路径 basetemp）。

## 真机冒烟（2026-08-06）

- clean 档：root + /workspace 只读（touch 失败）✅
- work 档：pip install six 经白名单代理成功；同卷跨命令 `pip show six` 命中 ✅
- 出站拦截：未知域名被代理拒绝（403 / Egress denied）✅
- Draft 冒烟：临时 git 仓库 create → 改副本文件 → status → merge 后主仓库
  出现改动 → discard 后副本目录消失 ✅
- profile 冒烟：deny 的 .env/secrets 不可读、/mnt/ro-0 与 /mnt/rw-0 就位、
  /scratch 可写 ✅；net 复用容器 exec 成功 ✅
- 环境注意：Docker Hub 直连不通时，需在 sandbox.toml 配置可达镜像
  （如 `image = "docker.1ms.run/library/python:3.12-slim"`）。

## 边界与 v2

- 沙箱为 Linux 容器：Windows 原生命令（cmd/dir/type）在沙箱内不可用。
- 出站白名单只约束走代理的工具；裸 socket 不在 v1 覆盖。
- v2 全部落地；后续可选：容器级 seccomp/更强制网络隔离。
