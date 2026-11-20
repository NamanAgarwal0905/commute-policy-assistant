"""Manual seeding:  python -m scripts.seed_data   (the app also does this at startup if AUTO_SEED=true)"""
from app.core.container import get_container
from app.services.seed import seed

if __name__ == "__main__":
    seed(get_container())
    print("seeded (idempotent)")
