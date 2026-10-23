# 上传到自己的 GitHub

本项目已包含 `.gitignore` 和自动测试流程。下面的步骤在 Windows PowerShell 执行，只需首次上传时完整操作一次。

本机项目已初始化为 `main` 分支，尚未创建首次提交。下面的 `git init` 在此目录可以跳过；新副本尚未初始化时再执行。

## 首次上传（推荐先建私有仓库）

电脑需要 Git 和 GitHub CLI（`gh`）。登录时选择 GitHub.com、HTTPS，并按提示在浏览器完成授权；不要把 GitHub 密码或令牌写进代码。

```powershell
cd D:\Pycharm_py\evil-code
gh auth login
git init -b main
git add .
git diff --cached --stat
git status --short
```

检查暂存列表后再提交。虚拟环境、缓存、本机代理配置、环境文件、私钥和手工测试目录已被忽略；项目文档、源码及测试会上传。自定义命名的凭证文件仍需自行检查。

首次使用 Git 时，如果提交提示缺少作者信息，设置本仓库的作者信息。把占位值替换为自己的姓名和邮箱；邮箱可用 GitHub Settings → Emails 中提供的隐私邮箱。

```powershell
git config user.name "你的名字"
git config user.email "你的GitHub邮箱或隐私邮箱"
```

创建本地提交，再创建名为 `evil-code` 的私有远程仓库并上传：

```powershell
git commit -m "Initial project baseline"
gh repo create evil-code --private --source=. --remote=origin --push
gh repo view --web
```

如果 `evil-code` 已存在，可选择不同的新名字。若已经手动创建了一个空仓库，跳过 `gh repo create`，改为：

```powershell
git remote add origin https://github.com/你的用户名/你的仓库名.git
git push -u origin main
```

手动创建远程空仓库时，不要添加 README、许可证或 `.gitignore`，以免和本地首次提交发生冲突。不要使用强制推送覆盖一个已有内容的远程仓库。

## 后续修改

```powershell
git switch -c fix/本次修改名称
# 完成修改后：
.\.venv-dev\Scripts\python.exe -m pytest -q --tb=short
git add .
git diff --cached --stat
git commit -m "描述这次修改"
git push -u origin HEAD
```

然后在 GitHub 上创建 Pull Request，检查 Actions 自动测试结果，再合并到 `main`。

GitHub 官方操作说明：https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github
