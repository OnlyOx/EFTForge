"""Hand-written, idempotent schema upgrades for databases created by older versions.

Each one inspects the live table and only adds what is missing, so running them
on every startup is safe."""

from sqlalchemy import text

from database import engine
from database_builds import builds_engine


def migrate_builds_db():
    with builds_engine.connect() as conn:
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(public_builds)"))}
        if "ammo_id" not in existing:
            conn.execute(text("ALTER TABLE public_builds ADD COLUMN ammo_id TEXT"))
            conn.commit()
        if "is_rotating" not in existing:
            conn.execute(text("ALTER TABLE public_builds ADD COLUMN is_rotating INTEGER NOT NULL DEFAULT 0"))
            conn.commit()
        if "user_display_name" not in existing:
            conn.execute(text("ALTER TABLE public_builds ADD COLUMN user_display_name TEXT"))
            conn.commit()
        if "user_avatar_url" not in existing:
            conn.execute(text("ALTER TABLE public_builds ADD COLUMN user_avatar_url TEXT"))
            conn.commit()
        if "tags_json" not in existing:
            conn.execute(text("ALTER TABLE public_builds ADD COLUMN tags_json TEXT"))
            conn.commit()

        ann_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(server_announcements)"))}
        if "dismissible" not in ann_cols:
            conn.execute(text("ALTER TABLE server_announcements ADD COLUMN dismissible INTEGER NOT NULL DEFAULT 1"))
            conn.commit()

        comment_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(build_comments)"))}
        if "user_display_name" not in comment_cols:
            conn.execute(text("ALTER TABLE build_comments ADD COLUMN user_display_name TEXT"))
            conn.commit()
        if "user_avatar_url" not in comment_cols:
            conn.execute(text("ALTER TABLE build_comments ADD COLUMN user_avatar_url TEXT"))
            conn.commit()

        # Indexes for hot query paths (create_all only adds them to new tables)
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_public_builds_ip_hash ON public_builds (ip_hash)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_build_vote_created_at ON build_votes (created_at)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_build_comments_ip_hash ON build_comments (ip_hash)"))
        conn.commit()


def migrate_items_db():
    with engine.connect() as conn:
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(items)"))}
        if "task_unlock_id" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN task_unlock_id TEXT"))
            conn.commit()
        if "task_unlock_name" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN task_unlock_name TEXT"))
            conn.commit()
        if "task_unlock_name_zh" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN task_unlock_name_zh TEXT"))
            conn.commit()
        if "sighting_range" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN sighting_range INTEGER"))
            conn.commit()
        if "bare_image_512_link" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN bare_image_512_link TEXT"))
            conn.commit()
        if "accuracy_modifier" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN accuracy_modifier REAL"))
            conn.commit()
        if "base_image_link" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN base_image_link TEXT"))
            conn.commit()
        if "ammo_damage" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN ammo_damage INTEGER"))
            conn.commit()
        if "penetration_power" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN penetration_power INTEGER"))
            conn.commit()
        if "armor_damage" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN armor_damage INTEGER"))
            conn.commit()
        if "velocity" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN velocity REAL"))
            conn.commit()
        if "tracer" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN tracer INTEGER"))
            conn.commit()
        if "tracer_color" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN tracer_color TEXT"))
            conn.commit()
        if "ammo_type" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN ammo_type TEXT"))
            conn.commit()
        if "projectile_count" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN projectile_count INTEGER"))
            conn.commit()
        if "fragmentation_chance" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN fragmentation_chance REAL"))
            conn.commit()
        if "ricochet_chance" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN ricochet_chance REAL"))
            conn.commit()
        if "stack_max_size" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN stack_max_size INTEGER"))
            conn.commit()
        if "ammo_accuracy_modifier" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN ammo_accuracy_modifier REAL"))
            conn.commit()
        if "ammo_recoil_modifier" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN ammo_recoil_modifier REAL"))
            conn.commit()
        if "light_bleed_delta" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN light_bleed_delta REAL"))
            conn.commit()
        if "heavy_bleed_delta" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN heavy_bleed_delta REAL"))
            conn.commit()
        if "penetration_chance" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN penetration_chance REAL"))
            conn.commit()
        if "penetration_power_deviation" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN penetration_power_deviation REAL"))
            conn.commit()
        if "heat_factor" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN heat_factor REAL"))
            conn.commit()
        if "cooling_factor" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN cooling_factor REAL"))
            conn.commit()
        if "durability_burn_factor" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN durability_burn_factor REAL"))
            conn.commit()
        if "velocity_modifier" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN velocity_modifier REAL"))
            conn.commit()
        if "loudness" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN loudness INTEGER"))
            conn.commit()
        if "category_ids" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN category_ids TEXT"))
            conn.commit()
        if "attachment_category" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN attachment_category TEXT"))
            conn.commit()
        if "attachment_category_zh" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN attachment_category_zh TEXT"))
            conn.commit()
        if "fire_rate" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN fire_rate INTEGER"))
            conn.commit()
        if "recoil_damping_hand_rot" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN recoil_damping_hand_rot REAL"))
            conn.commit()
        if "recoil_return_path_damping" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN recoil_return_path_damping REAL"))
            conn.commit()
        if "recoil_return_path_offset" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN recoil_return_path_offset REAL"))
            conn.commit()
        if "recoil_stable_index_shot" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN recoil_stable_index_shot INTEGER"))
            conn.commit()
        if "recoil_stable_angle_step" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN recoil_stable_angle_step REAL"))
            conn.commit()
        if "recoil_stable_angle" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN recoil_stable_angle REAL"))
            conn.commit()
        if "recoil_pos_z_mult" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN recoil_pos_z_mult REAL"))
            conn.commit()
        if "recoil_center_y" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN recoil_center_y REAL"))
            conn.commit()
        if "recoil_center_z" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN recoil_center_z REAL"))
            conn.commit()
        if "penetration_damage_mod" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN penetration_damage_mod REAL"))
            conn.commit()
        if "malf_feed_chance" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN malf_feed_chance REAL"))
            conn.commit()
        if "misfire_chance" not in existing:
            conn.execute(text("ALTER TABLE items ADD COLUMN misfire_chance REAL"))
            conn.commit()


def migrate_slots_db():
    with engine.connect() as conn:
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(slots)"))}
        if "slot_game_name" not in existing:
            conn.execute(text("ALTER TABLE slots ADD COLUMN slot_game_name TEXT"))
            conn.commit()
        if "required" not in existing:
            conn.execute(text("ALTER TABLE slots ADD COLUMN required BOOLEAN DEFAULT 0"))
            conn.commit()


def migrate_item_offers_db():
    with engine.connect() as conn:
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(item_offers)"))}
        if "game_mode" not in existing:
            conn.execute(text("ALTER TABLE item_offers ADD COLUMN game_mode TEXT"))
            conn.commit()
