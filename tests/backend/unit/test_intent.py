from sillytavern_rpg_engine.orchestration.intent import Intent, route_intent


def test_explicit_change_patterns():
    assert route_intent("给艾琳新增技能炼金术,当前35") is Intent.EXPLICIT_CHANGE
    assert route_intent("把艾琳的信任调整为60") is Intent.EXPLICIT_CHANGE
    assert route_intent("新增人物艾琳:半精灵炼金术师") is Intent.EXPLICIT_CHANGE
    assert route_intent("set alchemy to 35") is Intent.EXPLICIT_CHANGE
    assert route_intent("确认提案 p-102") is Intent.EXPLICIT_CHANGE
    assert route_intent("reject abc123") is Intent.EXPLICIT_CHANGE


def test_query_patterns():
    assert route_intent("查询艾琳的属性") is Intent.QUERY
    assert route_intent("查看背包") is Intent.QUERY
    assert route_intent("status") is Intent.QUERY


def test_everything_else_is_action():
    assert route_intent("我走进炼金铺,打量四周。") is Intent.ACTION
    assert route_intent("艾琳的信任是多少?") is Intent.ACTION  # 剧情内提问
    assert route_intent("") is Intent.ACTION
