"""The public price page's parser against the layout of the 2026-11 page: footnote marker on the model name, the concurrency row inside the table, and a Search & Fetch section."""

import pytest
from app import cny_prices

POLICY = (
    "<p>2026 年 11 月 1 日 00:00 起生效</p>"
    "<p>高峰时段：北京时间周一至周五（不含中国法定节假日）9:00 - 12:00、14:00 - 18:00</p>"
)


def cells(*c, tag="td"):
    return "<tr>" + "".join(f"<{tag}>{x}</{tag}>" for x in c) + "</tr>"


def page(names=("deepseek-flash(1)", "deepseek-v4-pro"), extra_after="", before=""):
    """The zh-cn page as published 2026-11-05 (flash first, pro second)."""
    rows = [
        cells("模型", *names, tag="th"),
        cells("模型版本", "DeepSeek-V4.1-Flash-1017", "DeepSeek-V4-Pro-0813"),
        cells("价格(2)", "百万tokens输入 (缓存命中)", "空闲时段", "0.02元", "0.15元"),
        cells("高峰时段", "0.04元", "0.30元"),
        cells("百万tokens输入 (缓存未命中)", "空闲时段", "1元", "4.5元"),
        cells("高峰时段", "2元", "9.0元"),
        cells("百万tokens输出", "空闲时段", "4元", "13.5元"),
        cells("高峰时段", "8元", "27.0元"),
        cells("并发限制(3)", "2500", "500"),
    ]
    return f"<html><body>{before}{POLICY}<table>{''.join(rows)}</table>{extra_after}</body></html>"


SEARCH_FETCH = "<h2>网页搜索与抓取价格</h2><table>" + cells("项目", "计费方式", "价格", tag="th") + cells("Search", "按请求次数", "5.4元") + cells("Fetch", "按请求次数", "5.4元") + "</table>"
NOTICE = "<p>Search / Fetch API 公告</p>"


def test_real_page_gives_flash_its_six_prices_and_ignores_concurrency_and_search_fetch():
    out = cny_prices.parse(page(extra_after=SEARCH_FETCH, before=NOTICE))
    assert [r["model_id"] for r in out] == ["deepseek-flash", "deepseek-v4-pro"]
    assert [r["model_version"] for r in out] == ["DeepSeek-V4.1-Flash-1017", "DeepSeek-V4-Pro-0813"]
    flash, pro = out[0]["rules"], out[1]["rules"]
    assert flash["off_peak"] == dict(cache_hit="0.02", cache_miss="1", output="4")
    assert flash["peak"] == dict(cache_hit="0.04", cache_miss="2", output="8")
    assert pro["peak"]["output"] == "27" and pro["off_peak"]["cache_miss"] == "4.5"
    assert flash["network_tools_enabled"] is False


def test_a_missing_or_reshaped_search_fetch_section_does_not_block_model_prices():
    assert len(cny_prices.parse(page())) == 2
    assert len(cny_prices.parse(page(extra_after=SEARCH_FETCH.replace("按请求次数", "每次")))) == 2


@pytest.mark.parametrize("raw", ["deepseek-flash（1）", "deepseek-flash [1]", "deepseek-flash(1)"])
def test_footnote_marker_styles(raw):
    assert cny_prices.parse(page((raw, "deepseek-v4-pro")))[0]["model_id"] == "deepseek-flash"


def test_a_really_changed_table_still_fails_with_a_named_reason():
    html = page().replace("缓存未命中", "未命中")
    with pytest.raises(ValueError, match="official_price_layout_changed"):
        cny_prices.parse(html)


def test_a_cosmetic_trailing_zero_is_not_a_changed_price():
    a = cny_prices.parse(page())[1]["rules"]
    b = cny_prices.parse(page().replace("9.0元", "9元").replace("27.0元", "27元"))[1]["rules"]
    assert a == b and a["peak"]["cache_miss"] == "9" and cny_prices.parse(page().replace("1元", "1.00元"))[0]["rules"]["off_peak"]["cache_miss"] == "1"
