"""M1-01 CMDB 四层模型的自动断言：落地形态、软删语义、唯一性作用域、FK 删略。

§0.7 双相纪律在此的落法：**"软删后同名可复用"必须被证明是那条 `WHERE NOT is_deleted`
换来的**，而不是"表上压根没有唯一约束"。二者的区别是全部价值所在——
没有约束时同名当然能复用，但那意味着活着的重名也放行（§1.3 的命名/层级唯一性失效）。
故 `test_plain_unique_index_breaks_reuse` 在事务内把索引换成无谓词版本，
断言复用**必须失败**：该用例红 ⇒ 部分谓词失效；它绿而其余用例红 ⇒ 谓词被误删。

注：本文件断言的是**模型层**事实。Admin 的"增删改查"（DoD 的人工观察项）
不在此重复——那是 M1-01 的验收动作，不是自动断言的对象。
"""

import pytest
from django.db import IntegrityError, connection, models, transaction

from apps.cmdb.models import CmdbApp, CmdbEnv, CmdbHost, CmdbSystem

UNIQ_SYSTEM = "uniq_cmdb_system_name_alive"


def _system(name="电子银行"):
    return CmdbSystem.objects.create(name=name, owner="张三")


def _app(system, name="order-center", **kw):
    return CmdbApp.objects.create(system=system, name=name, deploy_mode="host", **kw)


@pytest.mark.django_db
def test_four_tables_and_partial_unique_indexes_landed():
    """落地形态：四张表在真库里，且四条业务唯一约束**都是部分索引**。

    这条断言把"sqlmigrate 看着对"升级为"PG 里确实长这样"——ORM 声明与 DDL 之间
    隔着一层迁移编译器，只有查系统目录才作数。
    """
    with connection.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE tablename LIKE 'cmdb_%' ORDER BY 1")
        tables = [r[0] for r in cur.fetchall()]
        assert tables == ["cmdb_app", "cmdb_env", "cmdb_host", "cmdb_system"]

        cur.execute("""
            SELECT indexname, indexdef FROM pg_indexes
            WHERE tablename LIKE 'cmdb_%' AND indexname LIKE 'uniq_cmdb_%'
            ORDER BY indexname
            """)
        idx = cur.fetchall()
    assert len(idx) == 4, idx
    for name, ddl in idx:
        assert "UNIQUE" in ddl.upper(), (name, ddl)
        assert "WHERE" in ddl.upper() and "is_deleted" in ddl, f"{name} 不是部分索引：{ddl}"


@pytest.mark.django_db
def test_soft_deleted_name_can_be_reused_and_row_survives():
    """软删的主诉求：名字可复用 ∧ 旧行留表可审计——两件事必须同时成立。"""
    old = _system("电子银行")
    old_pk = old.pk
    old.delete()  # AbstractBaseModel.delete = 软删

    assert not CmdbSystem.objects.filter(pk=old_pk).exists(), "默认 Manager 不该看见软删行"
    dead = CmdbSystem.all_objects.get(pk=old_pk)
    assert dead.is_deleted is True and dead.deleted_at is not None, "软删行必须留痕"

    new = _system("电子银行")  # 同名的活行：部分唯一索引放行
    assert new.pk != old_pk


@pytest.mark.django_db
def test_alive_duplicate_is_still_rejected():
    """另一半：谓词只管软删行——**活着的重名仍必须被拒**，否则部分索引就成了没约束。"""
    _system("电子银行")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _system("电子银行")


@pytest.mark.django_db
def test_plain_unique_index_breaks_reuse():
    """反相（§0.7）：把部分索引换成无谓词的普通唯一索引，"同名可复用"必须立刻失效。

    这是本文件里唯一一条**自证能咬人**的用例：它证明复用不是"表上没约束"的副产品，
    而是那条 `WHERE NOT is_deleted` 精确买来的。
    """
    old = _system("电子银行")
    old.delete()

    with connection.cursor() as cur:
        try:
            cur.execute(f"DROP INDEX {UNIQ_SYSTEM}")
            cur.execute(f"CREATE UNIQUE INDEX {UNIQ_SYSTEM} ON cmdb_system (name)")  # 故意去掉谓词
            with pytest.raises(IntegrityError):
                with transaction.atomic():
                    _system("电子银行")
        finally:  # 恢复正确形态：即便断言失败，也不给后续用例留脏环境
            cur.execute(f"DROP INDEX IF EXISTS {UNIQ_SYSTEM}")
            cur.execute(
                f"CREATE UNIQUE INDEX {UNIQ_SYSTEM} ON cmdb_system (name) WHERE NOT is_deleted"
            )


@pytest.mark.django_db
def test_app_uniqueness_scope_is_per_system():
    """唯一性作用域：`(system, name)`——跨系统同名可共存，同系统内重名被拒。"""
    a, b = _system("电子银行"), _system("电子渠道")
    _app(a, "order-center")
    _app(b, "order-center")  # 跨系统同名：放行
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _app(a, "order-center")


@pytest.mark.django_db
def test_fk_on_delete_is_protect_across_four_levels():
    """四层 FK 一律 PROTECT（design-notes §1.2 对 `Ticket.app/env` 的口径）。

    元数据断言而非真删实验：`tickets` 表尚未诞生，构造不出真实的引用方；
    等 M1-09 就位后，"被单据引用即删不掉"由那边的用例背书——此处只钉住声明。
    """
    for field in (
        CmdbApp._meta.get_field("system"),
        CmdbEnv._meta.get_field("app"),
        CmdbHost._meta.get_field("env"),
    ):
        assert field.remote_field.on_delete is models.PROTECT, field.name


@pytest.mark.django_db
def test_defaults_and_array_fields():
    """默认值口径：数组字段默认**空列表**（不是 None）、env 三个门禁/预算字段的默认值。"""
    sys_obj = _system()
    app_obj = CmdbApp.objects.create(system=sys_obj, name="order-center", deploy_mode="host")
    env_obj = CmdbEnv.objects.create(app=app_obj, name="prod", tier="prod")

    assert app_obj.log_error_keywords == [] and app_obj.log_error_keywords is not None
    assert app_obj.health_expect_code == 200
    assert env_obj.auto_approve is False
    assert env_obj.min_hosts == 1
    assert env_obj.variables == {}

    host = CmdbHost.objects.create(env=env_obj, hostname="web-01", ip="10.0.0.11")
    assert host.tags == [], "tags 默认空列表——None 会让后续的 `in`/`len` 分支各写一遍"
    assert host.updated_at is not None, "§2.7 STALE_INVENTORY 依赖 host.updated_at"
