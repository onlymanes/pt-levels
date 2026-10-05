# pt-levels —— AI 量化支撑压力位展示站

公开地址（启用 Pages 后）：https://onlymanes.ai/pt-levels/

## 这是什么

静态展示网站：输入标的代码，查看日 K 线 + AI 量化支撑位 / 压力位 +
状态提示（正常 / 注意 / 信号异常建议回避）。

覆盖标的由 `tickers.txt`（一行一个）决定，构建时生成 `data/tickers.json`
供前端做输入校验与自动补全；改名单后提交即生效，下次自动构建计算新增标的。

## 保密设计

- 本仓库是**公开**的，只含计算**结果**（`data/*.json`）与前端。
- 算法源码（`volume_profile.py`）存放在**私有**仓库 `onlymanes/pt-engine-private`，
  只在 GitHub Actions 构建时拉取，**永不进入本仓库**。
- 对外字段全部中性命名：`support / resistance / warning`，无算法词汇、无参数、
  无中间分布。已做自动化泄露检查（见下）。

## 数据更新

- `scripts/build_data.py`：拉取日线 → 调用私有算法 → 生成 `data/*.json`。
- `.github/workflows/daily.yml`：每个美股交易日收盘后自动运行并提交，
  Pages 自动重新部署。构建时通过只读 deploy key 拉取私有算法仓库，
  所需 secrets（`PROD_REPO_SSH_KEY`）由部署脚本自动配置。
- 手动本地更新：`PROD_DIR=<算法目录> python scripts/build_data.py`

## 本地预览

```bash
cd pt-levels && python3 -m http.server 8000
# 浏览器打开 http://127.0.0.1:8000
```

## 泄露检查

```bash
grep -rniE "volume_profile|PT1|PT2|anchor|锚点|分位|对数正态|lognorm|select_window|build_profile|rolling_regime" \
  index.html data/ lightweight-charts.standalone.production.js
# 应无输出
```

## 上线步骤（已由部署脚本自动完成，手动备份如下）

1. GitHub 新建**公开**仓库 `onlymanes/pt-levels`，推送本目录全部内容。
2. 新建**私有**仓库 `onlymanes/pt-engine-private`，放入 `volume_profile.py`。
3. 给 `pt-engine-private` 加只读 deploy key；在 `pt-levels` 仓库
   Settings → Secrets → Actions 新建 `PROD_REPO_SSH_KEY`（私钥内容）。
4. `pt-levels` 仓库 Settings → Pages → Deploy from branch → `main` → `/(root)`。
5. Actions 页手动触发一次 `每日更新支撑压力位数据`，确认 `data/*.json` 已更新。
6. 访问 https://onlymanes.ai/pt-levels/ 验证。

## 免责

页面数据仅供研究参考，不构成投资建议。
