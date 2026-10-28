# USTC Adaptive Quiz

肖涵自定义强化训练：双学科各14题75分，全卷28题150分，实际开始后限时60分钟。

仅启用28题正式训练；旧60题生成、提交、固定间隔复习已退役，历史试卷、答案与成绩只读保留。同一个FastAPI服务提供 `/` 和 `/training`。这不是中科大官方考试固定结构。

每科固定3道单选、3道填空、4道名词解释、3道简答、1道论述。不存在每日60题产品或双模式切换；旧记录不能作为恢复旧模式的依据。

[唯一现行产品规格](docs/product-specification.md) · 公开规格接口：`GET /v2/training-specification`。

[接口、证据、运行与回滚说明](docs/reviewed-quiz.md) · [实际验收与外部依赖状态](docs/acceptance.md)

[动态命题、证据边界与可重放设计](docs/dynamic-engine.md)

[当前六文件OCR-only预检、导入与交接](docs/private-source-intake.md)
