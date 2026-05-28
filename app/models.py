from sqlalchemy import JSON, Column, DateTime, Float, Integer, String, UniqueConstraint, func

from app.database import Base


class Project(Base):
    __tablename__ = "projects"

    pid = Column(String, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    data = Column(JSON, nullable=False)
    created_at = Column(DateTime, server_default=func.current_timestamp())


class Zoning(Base):
    __tablename__ = "zoning"

    id = Column(Integer, primary_key=True, autoincrement=True)
    county = Column(String, nullable=False)
    district = Column(String, nullable=False)
    urban_plan = Column(String, nullable=False)
    land_use_zone = Column(String, nullable=False)
    building_coverage = Column(Float, nullable=False)
    floor_area_ratio = Column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "county", "district", "urban_plan", "land_use_zone",
            name="uq_zoning_lookup",
        ),
    )


class PriceIndex(Base):
    __tablename__ = "price_index"

    id = Column(Integer, primary_key=True, autoincrement=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    price_idx = Column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("year", "month", name="uq_price_index_year_month"),
    )
