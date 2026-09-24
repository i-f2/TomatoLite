"""S0-7 §0.7 并发测试基建自证：「锁测试全绿」必须等于「锁真的被测了」。

§0.7 钦定的陷阱：Django 默认 `TestCase`（pytest-django 的 `django_db` 不带
`transaction=True`）把用例包在外层事务里回滚——**线程各自开连接、看不见未提交
行**，锁断言跑在"什么都没有"之上，永远绿却什么都没测。本文件用三件事把这张
嘴堵上：

1. `test_fake_lock_race_is_caught`——故意用无锁裸读改写（竞态窗明置 0.1s），
   真实线程下缺陷**必须被观测到**（多赢家）。它就是 design-notes §0.7 说的
   "假锁测试能失败"的正体：基建若说谎（线程没真并发/没连真库），此断言先红。
2. `test_real_lock_gives_exactly_one_winner`——同场景 `SELECT … FOR UPDATE`，
   恰好一赢家。1+2 成对（§0.7 双相）⇒ 本基建对锁的有无**可分辨**。
3. `test_rollback_mode_hides_rows_from_threads`——陷阱实证：回滚模式下行对
   线程不可见 ⇒ 那种写法下的锁测试全是空测试（这正是 1 里 transaction=True
   不可省略的原因）。

真实边界：真实 PG（vendor 前置断言，S0-8 承接全局闸）、真实线程、真实连接、
barrier 对齐起跑。演示表 raw SQL 建/删，不落任何 model/migration。
"""

import threading
import time

import pytest
from django.db import connection, transaction

N_THREADS = 8
DEMO_TABLE = "tl_s07_lockrow"


@pytest.fixture(autouse=True)
def _require_postgresql():
    # §0.6/§0.7：锁语义只在真 PG 上成立。SQLite 下本文件必须炸而不是绕。
    assert (
        connection.vendor == "postgresql"
    ), f"lock infra proof requires PostgreSQL, got {connection.vendor}"


def _setup_table():
    with connection.cursor() as c:
        c.execute(f"DROP TABLE IF EXISTS {DEMO_TABLE}")
        c.execute(
            f"CREATE TABLE {DEMO_TABLE} ("
            f"  id int PRIMARY KEY,"
            f"  taken bool NOT NULL DEFAULT false,"
            f"  wins int NOT NULL DEFAULT 0)"
        )
        c.execute(f"INSERT INTO {DEMO_TABLE} (id) VALUES (1)")


def _teardown_table():
    with connection.cursor() as c:
        c.execute(f"DROP TABLE IF EXISTS {DEMO_TABLE}")


def _race(fn):
    """N 线程 barrier 对齐起跑，返回 fn 的逐线程返回值。挂起=失败（超时断言）。"""
    results = []
    results_lock = threading.Lock()
    barrier = threading.Barrier(N_THREADS, timeout=10)

    def body():
        try:
            barrier.wait()
            r = fn()
            with results_lock:
                results.append(r)
        finally:
            connection.close()  # 线程退出前放掉自己的连接

    threads = [threading.Thread(target=body) for _ in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "线程挂起=基建失效，绝不静默"
    assert len(results) == N_THREADS
    return results


def _fake_claim():
    """缺陷剧本：SELECT 判 taken 与 UPDATE 之间无锁无事务——教科书竞态。"""
    with connection.cursor() as cur:
        cur.execute(f"SELECT taken FROM {DEMO_TABLE} WHERE id=1")
        row = cur.fetchone()
    if row and not row[0]:
        time.sleep(0.1)  # 竞态窗明置：让缺陷可被确定性观测
        with connection.cursor() as cur:
            cur.execute(f"UPDATE {DEMO_TABLE} SET taken=true, wins=wins+1 WHERE id=1")
        return True
    return False


def _real_claim():
    """正确剧本：FOR UPDATE 把"判+改"整段锁进行。锁内慢动作不破坏互斥。"""
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(f"SELECT taken FROM {DEMO_TABLE} WHERE id=1 FOR UPDATE")
            row = cur.fetchone()
        won = bool(row) and not row[0]
        if won:
            time.sleep(0.05)
            with connection.cursor() as cur:
                cur.execute(f"UPDATE {DEMO_TABLE} SET taken=true, wins=wins+1 WHERE id=1")
    return won


def _final_wins():
    with connection.cursor() as cur:
        cur.execute(f"SELECT wins FROM {DEMO_TABLE} WHERE id=1")
        return cur.fetchone()[0]


@pytest.mark.django_db(transaction=True)
def test_fake_lock_race_is_caught():
    """§0.7 之"假锁测试必须能失败"：无锁剧本里多赢家**必须**被观测到。

    若本断言"意外通过不了"（只有 ≤1 赢家），说明线程根本没并发跑/没连到
    同一个库——那才是基建出鬼的信号。基建对缺陷可检，其后的绿才可信。
    """
    _setup_table()
    try:
        winners = sum(_race(_fake_claim))
        assert winners > 1, "基建未能观测到无锁竞态 ⇒ 线程面是假的，后续一切绿不可信"
        assert _final_wins() == winners  # 行计数与赢家数一致=观测自洽
    finally:
        _teardown_table()


@pytest.mark.django_db(transaction=True)
def test_real_lock_gives_exactly_one_winner():
    """同场景上锁 ⇒ 恰好一赢家。与 test_fake 成对：基建可分辨锁之有无。"""
    _setup_table()
    try:
        winners = sum(_race(_real_claim))
        assert winners == 1
        assert _final_wins() == 1  # 认领只发生一次，counter 无并发损耗
    finally:
        _teardown_table()


@pytest.mark.django_db(transaction=False)
def test_rollback_mode_hides_rows_from_threads():
    """陷阱本体可视化：默认回滚模式下，外层事务里的行对线程**不存在**。

    这种模式下写锁测试，线程面对的是空表/无表——断言再严也是空转（永远绿）。
    本测试断"看不见"，即 §0.7 的机制证据；transaction=True 非可选的理由。
    """
    with connection.cursor() as c:
        c.execute(f"DROP TABLE IF EXISTS {DEMO_TABLE}")
        c.execute(
            f"CREATE TABLE {DEMO_TABLE} (id int PRIMARY KEY, taken bool NOT NULL DEFAULT false,"
            f" wins int NOT NULL DEFAULT 0)"
        )
        c.execute(f"INSERT INTO {DEMO_TABLE} (id) VALUES (1)")
    with connection.cursor() as c:
        c.execute(f"SELECT count(*) FROM {DEMO_TABLE}")
        assert c.fetchone()[0] == 1  # 主连接（外层事务内）看得见

    def _probe():
        try:
            with connection.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM {DEMO_TABLE}")
                return cur.fetchone()[0]
        except Exception:
            return 0  # 表本身不可见，同属"看不见"
        finally:
            connection.close()

    seen = []
    lock = threading.Lock()

    def body():
        r = _probe()
        with lock:
            seen.append(r)

    threads = [threading.Thread(target=body) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads)
    assert seen and max(seen) == 0, "线程竟看见了未提交行——环境/基建与预期不符"
