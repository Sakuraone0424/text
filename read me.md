# USTC Adaptive Quiz

中科大考研刷题系统：生物化学 + 细胞生物学，每天一套 60 题、150 分、限时 60 分钟。

每科 30 题、75 分：18 道单选（每题 2 分）、7 道填空（2 分）、4 道名词解释（4 分）、1 道简答（9 分）。规格 ID `xiaohan-60q-150pt-v2`，代码在 `backend/app/training_spec_v2.py`。

同一个 FastAPI 服务提供 `/` 和 `/training`；旧版 28 题卷只读保留，可以打开和查看历史成绩。

[产品规格](docs/product-specification.md) · 公开规格接口：`GET /v2/training-specification`

[部署说明](docs/deployment.md)

## 本地测试

```sh
cd backend
pip install -r requirements-dev.txt
TEST_DATABASE_URL=postgresql://user:pass@127.0.0.1:5432/quiz_test python -m pytest -q
```
