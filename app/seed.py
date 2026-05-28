from app.database import Base, SessionLocal, engine
from app.models import Project, Zoning


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(Zoning).count() == 0:
            db.add_all([
                Zoning(
                    county="臺北市",
                    district="大安區",
                    urban_plan="臺北市都市計畫",
                    land_use_zone="第三種住宅區",
                    building_coverage=45.0,
                    floor_area_ratio=225.0,
                ),
                Zoning(
                    county="臺北市",
                    district="信義區",
                    urban_plan="臺北市都市計畫",
                    land_use_zone="商業區",
                    building_coverage=80.0,
                    floor_area_ratio=560.0,
                ),
            ])

        if db.query(Project).count() == 0:
            db.add(Project(
                pid="demo-pid-0001",
                user_id="demo-user-0001",
                data={
                    "name": "示範專案",
                    "owner": "demo",
                    "status": "draft",
                },
            ))

        db.commit()
    finally:
        db.close()
