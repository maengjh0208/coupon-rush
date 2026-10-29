from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    MetaData,
    Table,
    Text,
    func,
    UniqueConstraint,
)

metadata = MetaData()  # 테이블 정의

coupon = Table(
    "coupon",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),  # Identity: postgreSQL의 표준 자동 증가 방식
    Column("name", Text, nullable=False),
    Column("total_quantity", Integer, nullable=False),
    Column("issued_quantity", Integer, nullable=False, server_default="0"),
    Column("starts_at", DateTime(timezone=True), nullable=False),  # 시간은 항상 타임존을 포함해서 저장하기
)

coupon_issue = Table(
    "coupon_issue",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("coupon_id", BigInteger, ForeignKey("coupon.id"), nullable=False),
    Column("user_id", BigInteger, nullable=False),
    Column("issued_at", DateTime(timezone=True), nullable=False, server_default=func.now()),  # server_default=func.now() 은 DB 서버의 now()로 찍힘
    UniqueConstraint("coupon_id", "user_id", name="unique_coupon_issue_coupon_id_user_id"),
)
