"""数据库架构定义、迁移与初始默认值。

设计要点
--------
* 以 projects 为最高管理单元；分类、章节、优先级、状态、标签、负责人都按项目隔离。
* tasks.id 为 UUID 主键；task_code 仅为可选的显示编号，不唯一、不作主键。
* 父子关系使用 parent_id；工作模块（分类）使用 category_id，两者互不混淆。
* 外键：删除项目时级联删除其全部数据；删除任务时级联删除其评论、标签关联、
  依赖关系；删除父任务、分类、章节、优先级、状态时 **不** 级联（NO ACTION），
  必须由业务层先处理关联任务，从而杜绝静默级联删除。
* 状态与进度相互独立；状态通过 kind 字段表达统计语义（done / blocked …），
  名称可自由修改。
"""
from __future__ import annotations

SCHEMA_VERSION = 2

# 状态语义类别：名称可以随意修改，统计只依赖 kind
STATUS_KINDS = {
    "todo": "未开始类",
    "in_progress": "进行中类",
    "review": "待审核类",
    "done": "已完成类",
    "blocked": "阻塞类",
    "paused": "暂缓类",
    "cancelled": "已取消类（不计入统计）",
}

MIGRATIONS: dict[int, str] = {
    1: """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS categories (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    color       TEXT NOT NULL DEFAULT '#2563EB',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (project_id, name)
);

CREATE TABLE IF NOT EXISTS chapters (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    group_name  TEXT,
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (project_id, name)
);

CREATE TABLE IF NOT EXISTS priorities (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    color       TEXT NOT NULL DEFAULT '#6B7280',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (project_id, name)
);

CREATE TABLE IF NOT EXISTS statuses (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'todo'
                CHECK (kind IN ('todo','in_progress','review','done','blocked','paused','cancelled')),
    color       TEXT NOT NULL DEFAULT '#6B7280',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    is_default  INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (project_id, name)
);

CREATE TABLE IF NOT EXISTS people (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (project_id, name)
);

CREATE TABLE IF NOT EXISTS tags (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    color       TEXT NOT NULL DEFAULT '#6B7280',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (project_id, name)
);

CREATE TABLE IF NOT EXISTS tasks (
    id                  TEXT PRIMARY KEY,
    task_code           TEXT,
    project_id          TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    parent_id           TEXT REFERENCES tasks(id),
    title               TEXT NOT NULL CHECK (length(trim(title)) > 0),
    description         TEXT,
    deliverable         TEXT,
    prerequisites       TEXT,
    category_id         TEXT REFERENCES categories(id),
    chapter_id          TEXT REFERENCES chapters(id),
    priority_id         TEXT REFERENCES priorities(id),
    status_id           TEXT REFERENCES statuses(id),
    assignee            TEXT,
    progress            REAL NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
    progress_mode       TEXT NOT NULL DEFAULT 'manual' CHECK (progress_mode IN ('manual','auto')),
    weight              REAL NOT NULL DEFAULT 1 CHECK (weight > 0),
    due_date            TEXT,
    acceptance_criteria TEXT,
    notes               TEXT,
    source_file         TEXT,
    source_row          INTEGER,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    completed_at        TEXT,
    sort_order          INTEGER NOT NULL DEFAULT 0,
    CHECK (parent_id IS NULL OR parent_id <> id)
);

CREATE TABLE IF NOT EXISTS task_tags (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    tag_id  TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, tag_id)
);

CREATE TABLE IF NOT EXISTS task_people (
    task_id   TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, person_id)
);

CREATE TABLE IF NOT EXISTS task_dependencies (
    id                 TEXT PRIMARY KEY,
    task_id            TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    created_at         TEXT NOT NULL,
    UNIQUE (task_id, depends_on_task_id),
    CHECK (task_id <> depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS task_comments (
    id         TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_settings (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    value      TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, key)
);

CREATE TABLE IF NOT EXISTS import_log (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES projects(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    source      TEXT,
    task_count  INTEGER NOT NULL DEFAULT 0,
    report      TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_project   ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_parent    ON tasks(parent_id);
CREATE INDEX IF NOT EXISTS idx_tasks_category  ON tasks(category_id);
CREATE INDEX IF NOT EXISTS idx_tasks_chapter   ON tasks(chapter_id);
CREATE INDEX IF NOT EXISTS idx_tasks_priority  ON tasks(priority_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status    ON tasks(status_id);
CREATE INDEX IF NOT EXISTS idx_tasks_due       ON tasks(due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_code      ON tasks(project_id, task_code);
CREATE INDEX IF NOT EXISTS idx_categories_proj ON categories(project_id);
CREATE INDEX IF NOT EXISTS idx_chapters_proj   ON chapters(project_id);
CREATE INDEX IF NOT EXISTS idx_priorities_proj ON priorities(project_id);
CREATE INDEX IF NOT EXISTS idx_statuses_proj   ON statuses(project_id);
CREATE INDEX IF NOT EXISTS idx_people_proj     ON people(project_id);
CREATE INDEX IF NOT EXISTS idx_tags_proj       ON tags(project_id);
CREATE INDEX IF NOT EXISTS idx_task_tags_tag   ON task_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_task_people_p   ON task_people(person_id);
CREATE INDEX IF NOT EXISTS idx_dep_task        ON task_dependencies(task_id);
CREATE INDEX IF NOT EXISTS idx_dep_on          ON task_dependencies(depends_on_task_id);
CREATE INDEX IF NOT EXISTS idx_comments_task   ON task_comments(task_id);
CREATE INDEX IF NOT EXISTS idx_import_log_proj ON import_log(project_id);
""",
    # ------------------------------------------------------------------
    # V1.1：科研文件关联、任务工作历史、项目科研日志
    # 只新增表，不修改/删除任何既有表；原 task_comments 保留，其内容复制为 note 记录。
    # ------------------------------------------------------------------
    2: """
CREATE TABLE IF NOT EXISTS attachment_types (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    extensions  TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_attachments (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    attachment_type TEXT NOT NULL DEFAULT '其他',
    storage_type    TEXT NOT NULL CHECK (storage_type IN ('local', 'url')),
    file_path       TEXT,
    url             TEXT,
    description     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    CHECK ((storage_type = 'local' AND file_path IS NOT NULL) OR
           (storage_type = 'url' AND url IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS task_activity_logs (
    id            TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    activity_type TEXT NOT NULL,
    content       TEXT,
    old_value     TEXT,
    new_value     TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS project_daily_logs (
    id               TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    log_date         TEXT NOT NULL,
    title            TEXT NOT NULL,
    content          TEXT,
    done_work        TEXT,
    problems         TEXT,
    next_steps       TEXT,
    related_task_ids TEXT NOT NULL DEFAULT '[]',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_att_task        ON task_attachments(task_id);
CREATE INDEX IF NOT EXISTS idx_att_type        ON task_attachments(attachment_type);
CREATE INDEX IF NOT EXISTS idx_act_task_time   ON task_activity_logs(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_act_type        ON task_activity_logs(activity_type);
CREATE INDEX IF NOT EXISTS idx_daily_proj_date ON project_daily_logs(project_id, log_date);

INSERT OR IGNORE INTO task_activity_logs(id, task_id, activity_type, content, created_at, updated_at)
SELECT id, task_id, 'note', content, created_at, created_at FROM task_comments;
""",
}

# 文件类型初始值（名称, 扩展名）。可在“系统设置”中增加。
DEFAULT_ATTACHMENT_TYPES = [
    ("PDF", ".pdf"),
    ("Word", ".doc,.docx,.rtf"),
    ("Excel", ".xls,.xlsx,.xlsm,.csv"),
    ("PowerPoint", ".ppt,.pptx"),
    ("Python", ".py,.ipynb"),
    ("MATLAB", ".m,.mat,.fig,.mlx"),
    ("图片", ".png,.jpg,.jpeg,.svg,.gif,.bmp,.tif,.tiff,.eps,.webp"),
    ("数据文件", ".json,.txt,.dat,.h5,.hdf5,.npz,.npy,.parquet,.zip,.7z,.tar,.gz"),
    ("网页链接", ""),
    ("其他", ""),
]

# 工作历史记录类型
ACTIVITY_TYPES = {
    "progress_update": "进度更新",
    "status_change": "状态变更",
    "priority_change": "优先级变更",
    "due_date_change": "截止日期修改",
    "description_update": "任务内容修改",
    "milestone": "阶段性成果",
    "note": "工作记录",
}
MANUAL_ACTIVITY_TYPES = ("note", "milestone")

# 所有业务表（JSON 备份/恢复、校验时使用；顺序满足外键依赖）
DATA_TABLES = [
    "projects",
    "categories",
    "chapters",
    "priorities",
    "statuses",
    "people",
    "tags",
    "tasks",
    "task_tags",
    "task_people",
    "task_dependencies",
    "task_comments",
    "app_settings",
    "project_settings",
    "import_log",
    # V1.1
    "attachment_types",
    "task_attachments",
    "task_activity_logs",
    "project_daily_logs",
]

# ---------------------------------------------------------------------------
# 新项目的初始默认值（仅作为种子数据写入数据库，之后全部可在界面修改）
# ---------------------------------------------------------------------------
DEFAULT_PRIORITIES = [
    ("P0", "当前必须完成", "#DC2626"),
    ("P1", "论文核心需要", "#EA580C"),
    ("P2", "重要补充", "#2563EB"),
    ("P3", "长期研究", "#6B7280"),
]

# (名称, kind, 颜色, 是否默认)
DEFAULT_STATUSES = [
    ("待开始", "todo", "#9CA3AF", 1),
    ("进行中", "in_progress", "#2563EB", 0),
    ("待审核", "review", "#7C3AED", 0),
    ("已完成", "done", "#16A34A", 0),
    ("阻塞", "blocked", "#DC2626", 0),
    ("暂缓", "paused", "#A16207", 0),
]

DEFAULT_TAGS = [
    "核心论文",
    "图表",
    "数据核查",
    "需要人工确认",
    "待导师审核",
    "紧急",
    "未来研究",
]

# 全局设置（app_settings）默认值：与具体项目无关的界面与系统偏好
DEFAULT_GLOBAL_SETTINGS = {
    "current_project_id": None,
    "default_view": "table",
    "page_size": 50,
    "date_format": "%Y-%m-%d",
    "calendar_week_start": 0,  # 0=周一
    "upcoming_days": 14,
    "last_auto_backup": None,
}

# 项目设置（project_settings）默认值：每个项目独立
DEFAULT_PROJECT_SETTINGS = {
    "default_sort": "priority",
    # 整体完成率默认口径：A=任务完成率，B=叶子平均完成度，C=加权完成度
    "overall_metric": "B",
    # 统计口径：是否排除暂缓类任务
    "stats_exclude_paused": False,
    # 任务完成率中“已完成”的判定：status=按状态类别，progress=按完成度 100%
    "completion_basis": "status",
}
