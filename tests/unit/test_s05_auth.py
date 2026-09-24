"""S0-5 Step 1 断言（§0.7 反假绿）：User/Role 建模、session 登录回路、
软删即除权、system 强制 unusable、role 无默认必填、ROLE_RANK 序与 system 无秩。"""
import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError

pytestmark = pytest.mark.django_db


# ---- 建模与 Manager ----

def test_user_managers_and_table():
    from apps.core.models import SoftDeleteManager, User, UserManager

    mgrs = {m.name: type(m) for m in User._meta.managers}
    assert mgrs["objects"] is UserManager and mgrs["all_objects"] is not UserManager
    assert issubclass(UserManager, SoftDeleteManager)
    assert hasattr(User.objects, "get_by_natural_key")   # ModelBackend 登录协议件
    assert User._meta.db_table == "tl_user"
    assert User.objects.model is User
    # 软删口生效：默认 manager 的 get_queryset 带 is_deleted 谓词
    assert "is_deleted" in str(User.objects.get_queryset().query)


def test_role_field_required_no_default():
    from django.db.models.fields import NOT_PROVIDED

    from apps.core.models import User

    f = User._meta.get_field("role")
    assert f.default is NOT_PROVIDED and not f.has_default()   # 安全字段不兜底
    u = User(username="n1")
    with pytest.raises(ValidationError) as e:
        u.full_clean()
    assert "role" in e.value.message_dict                       # 漏 role 必炸校验


def test_role_set_and_rank_order():
    from apps.core.models import ROLE_RANK, Role, User

    assert {c[0] for c in Role.choices} == {
        "business", "dev", "ops", "admin", "system"}
    assert ROLE_RANK[Role.BUSINESS] < ROLE_RANK[Role.DEV] < ROLE_RANK[Role.OPS] < ROLE_RANK[Role.ADMIN]
    assert Role.SYSTEM not in ROLE_RANK                         # system 无秩，显式缺席
    with pytest.raises(ValidationError):
        User(username="n2", role="root").full_clean()           # 枚举外值拒


# ---- session 登录回路（拍板①：ModelBackend + 密码）----

def test_login_roundtrip():
    from apps.core.models import Role, User

    u = User.objects.create_user(username="alice", password="pw-alice-9", role=Role.OPS)
    assert authenticate(None, username="alice", password="pw-alice-9") == u
    assert authenticate(None, username="alice", password="wrong") is None
    assert authenticate(None, username="ghost", password="pw-alice-9") is None


def test_soft_delete_kills_auth():
    from apps.core.models import Role, User

    u = User.objects.create_user(username="bob", password="pw-bob-9", role=Role.DEV)
    assert authenticate(None, username="bob", password="pw-bob-9") == u
    u.delete()                                                  # 软删
    assert authenticate(None, username="bob", password="pw-bob-9") is None
    assert not User.objects.filter(username="bob").exists()     # 默认口不可见
    row = User.all_objects.get(username="bob")                  # 全量口仍在（非硬删）
    assert row.is_deleted and row.deleted_at is not None


# ---- system 服务身份（拍板②）----

def test_system_user_password_always_unusable():
    from apps.core.models import Role, User

    u = User(username="svc-ci", role=Role.SYSTEM, email="svc@tl.local")
    u.set_password("known-pw-9")                                # 调用方塞了密码
    u.save()
    assert not u.has_usable_password()                          # save() 强制覆为 unusable
    assert authenticate(None, username="svc-ci", password="known-pw-9") is None


def test_system_user_cannot_be_staff():
    from apps.core.models import Role, User

    u = User(username="svc-bad", role=Role.SYSTEM, is_staff=True)
    with pytest.raises(ValueError):
        u.save()
