"""Known mutual-answer cues, stable across draft revisions; not chapter weights."""

GROUPS = {
    "two_dimensional_electrophoresis": frozenset({"实验方法/二维电泳"}),
    "molecular_chaperone": frozenset({"蛋白质成熟/分子伴侣"}),
}


def conflict(question, others):
    groups = {
        key for key, tags in GROUPS.items() if tags & set(question["knowledge_tags"])
    }
    return any(
        question["subject"] == other["subject"]
        and any(GROUPS[key] & set(other["knowledge_tags"]) for key in groups)
        for other in others
    )
