"""S0-5 Step 2 断言：HasRole 角色×位点全枚举矩阵（200/401/403 含负例）、
envelope 有效性、role_at_least 序与 system 无秩、SoD 同人拒/异人放。

调用方式 = APIRequestFactory + view(request) 直调（受控视图不落 urls——S0-5
Non-Goal；异常面走 dispatch→handle_exception→全局 handler，与真实请求同链）。
"""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from apps.core.exceptions import AppError
from apps.core.models import Role, User
from apps.core.permissions import HasRole
from apps.core.rbac import enforce_separation_of_duties, role_at_least

pytestmark = pytest.mark.django_db


def _site(name, required):
    ns = {
        "permission_classes": [HasRole],
        "required_roles": required,
        "get": lambda self, request: Response({"ok": True}),
    }
    return type(name, (APIView,), ns).as_view()


def _public_site():
    """未声明权限类 = settings 默认（未配 DEFAULT_PERMISSION_CLASSES → AllowAny）
    ——拍板④"默认开放"的回归红线位（health 匿名 200 同构）。"""
    ns = {"get": lambda self, request: Response({"ok": True})}
    return type("PublicSite", (APIView,), ns).as_view()


def _no_declare_site():
    """HasRole 但忘声明 required_roles——必须炸配置错误，
    不许静默放行（越权写进全绿）也不许静默 403（误锁写进全绿）。"""
    ns = {
        "permission_classes": [HasRole],
        "get": lambda self, request: Response({"ok": True}),
    }
    return type("NoDeclareSite", (APIView,), ns).as_view()


def _call(view, user=None):
    request = APIRequestFactory().get("/")
    if user is not None:
        force_authenticate(request, user=user)
    return view(request)


@pytest.fixture
def users():
    made = {}
    for value in ("business", "dev", "ops", "admin"):
        made[value] = User.objects.create_user(
            username=f"u-{value}", password=f"pw-{value}-9", role=value
        )
    sysu = User(username="u-system", role=Role.SYSTEM)
    sysu.set_password("pw-system-9")
    sysu.save()
    made[Role.SYSTEM] = sysu
    return made


# ---- 1) 角色×位点全枚举矩阵（4 受控位点 × 5 角色 = 20 格，逐格判）----

SITES = {
    "dev_site": (Role.DEV, Role.OPS, Role.ADMIN),
    "ops_site": (Role.OPS, Role.ADMIN),
    "admin_site": (Role.ADMIN,),
    "system_site": (Role.SYSTEM,),  # 显式列出才放——system 不随任何门槛
}

MATRIX = [  # (site, role, expected_status, expected_code)
    ("dev_site", Role.BUSINESS, 403, "PERMISSION_DENIED"),
    ("dev_site", Role.DEV, 200, "OK"),
    ("dev_site", Role.OPS, 200, "OK"),
    ("dev_site", Role.ADMIN, 200, "OK"),
    ("dev_site", Role.SYSTEM, 403, "PERMISSION_DENIED"),
    ("ops_site", Role.BUSINESS, 403, "PERMISSION_DENIED"),
    ("ops_site", Role.DEV, 403, "PERMISSION_DENIED"),
    ("ops_site", Role.OPS, 200, "OK"),
    ("ops_site", Role.ADMIN, 200, "OK"),
    ("ops_site", Role.SYSTEM, 403, "PERMISSION_DENIED"),
    ("admin_site", Role.BUSINESS, 403, "PERMISSION_DENIED"),
    ("admin_site", Role.DEV, 403, "PERMISSION_DENIED"),
    ("admin_site", Role.OPS, 403, "PERMISSION_DENIED"),
    ("admin_site", Role.ADMIN, 200, "OK"),
    ("admin_site", Role.SYSTEM, 403, "PERMISSION_DENIED"),
    ("system_site", Role.BUSINESS, 403, "PERMISSION_DENIED"),
    ("system_site", Role.DEV, 403, "PERMISSION_DENIED"),
    ("system_site", Role.OPS, 403, "PERMISSION_DENIED"),
    ("system_site", Role.ADMIN, 403, "PERMISSION_DENIED"),
    ("system_site", Role.SYSTEM, 200, "OK"),
]


@pytest.mark.parametrize("site,role,status,code", MATRIX)
def test_role_site_matrix(users, site, role, status, code):
    r = _call(_site(f"{site}_{role}", SITES[site]), user=users[role])
    assert r.status_code == status
    if status == 200:
        assert r.data == {"ok": True}  # 成功体不归 handler 管（envelope 是错误面契约）
    else:
        assert r.data["code"] == code


@pytest.mark.parametrize("site", SITES)
def test_anonymous_gets_401_envelope(site):
    """匿名打受控位点 → 401，且必须是合法 envelope（不许裸奔 403/500）。"""
    r = _call(_site("anon_" + site, SITES[site]))
    assert r.status_code == 401
    assert set(r.data) == {"code", "message", "data", "request_id"}
    assert r.data["code"] == "NOT_AUTHENTICATED"
    assert r.data["data"] is None
    assert len(r.data["request_id"]) == 32
    int(r.data["request_id"], 16)


def test_403_envelope_valid(users):
    r = _call(_site("env403", SITES["admin_site"]), user=users[Role.BUSINESS])
    assert r.status_code == 403
    assert set(r.data) == {"code", "message", "data", "request_id"}
    assert r.data["code"] == "PERMISSION_DENIED"


def test_public_site_anonymous_200():
    r = _call(_public_site())
    assert r.status_code == 200
    assert r.data == {"ok": True}


def test_missing_required_roles_is_config_error(users):
    with pytest.raises(ImproperlyConfigured):
        _call(_no_declare_site(), user=users[Role.ADMIN])


# ---- 2) role_at_least ----


def test_role_at_least_ordering(users):
    assert role_at_least(users[Role.ADMIN], Role.BUSINESS)
    assert not role_at_least(users[Role.BUSINESS], Role.DEV)
    assert not role_at_least(users[Role.OPS], Role.ADMIN)
    assert role_at_least(users[Role.OPS], Role.OPS)  # 同秩放
    assert not role_at_least(users[Role.SYSTEM], Role.BUSINESS)  # 无秩恒 False
    assert not role_at_least(AnonymousUser(), Role.BUSINESS)


# ---- 3) SoD：发起人≠审批人（PRD §3 硬约束）----


def test_sod_rejects_same_person(users):
    ops = users[Role.OPS]
    with pytest.raises(AppError) as e:
        enforce_separation_of_duties(actor=ops, owner=ops)  # 同人（换会话仍同 pk）拒
    assert e.value.status_code == 403
    assert e.value.code_slug == "SEPARATION_OF_DUTIES"


def test_sod_rejects_below_ops(users):
    with pytest.raises(AppError):
        enforce_separation_of_duties(actor=users[Role.DEV], owner=users[Role.BUSINESS])


def test_sod_rejects_system_actor(users):
    """system 无秩：不得充当人工审批主体（秩要件必拒）。"""
    with pytest.raises(AppError):
        enforce_separation_of_duties(actor=users[Role.SYSTEM], owner=users[Role.BUSINESS])


def test_sod_allows_distinct_qualified(users):
    assert enforce_separation_of_duties(actor=users[Role.OPS], owner=users[Role.BUSINESS]) is None
    assert enforce_separation_of_duties(actor=users[Role.ADMIN], owner=users[Role.OPS]) is None


def test_sod_rejects_missing_actor(users):
    with pytest.raises(AppError):
        enforce_separation_of_duties(actor=None, owner=users[Role.BUSINESS])
    with pytest.raises(AppError):
        enforce_separation_of_duties(actor=users[Role.OPS], owner=None)
