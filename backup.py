"""
backup.py — HF Hub 数据备份脚本

将 /root/.openclaw 目录上传到 Hugging Face Dataset 仓库，
实现配置、对话历史和 Agent 状态的持久化。

调用时机（由 start_hf.sh 控制）：
  1. 容器收到 SIGTERM/SIGINT 时（优雅关闭前）
  2. 后台定时任务每隔 BACKUP_INTERVAL_MIN 分钟自动触发

环境变量：
  HF_DATASET           — HF Dataset 仓库 ID，如 "your-user/openclaw-backup"
  HF_TOKEN             — Hugging Face 访问令牌（需要 write 权限）
  BACKUP_INTERVAL_MIN  — 自动备份间隔（分钟，默认 30）

若 HF_DATASET / HF_TOKEN 未设置，脚本静默跳过。
"""
import os
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [backup] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('backup')

HF_DATASET = os.environ.get('HF_DATASET', '').strip()
HF_TOKEN   = os.environ.get('HF_TOKEN',   '').strip()
LOCAL_DIR  = '/root/.openclaw'

# 不上传的路径（日志、临时文件、依赖包、版本库）
IGNORE_PATTERNS = [
    '*.log',
    'tmp/*',
    '**/node_modules/**',
    '**/.git/**',
]

if not HF_DATASET or not HF_TOKEN:
    log.warning(
        '⚠️  HF_DATASET 或 HF_TOKEN 未设置，跳过备份步骤。\n'
        '   若环境变量被意外清除，数据将不会持久化到 HF Hub，请检查 Spaces 密钥配置。'
    )
else:
    try:
        from huggingface_hub import upload_folder
        log.info('开始备份至 HF Hub: %s → %s', LOCAL_DIR, HF_DATASET)
        upload_folder(
            folder_path=LOCAL_DIR,
            repo_id=HF_DATASET,
            repo_type='dataset',
            token=HF_TOKEN,
            ignore_patterns=IGNORE_PATTERNS,
        )
        log.info('✅ 备份完成: %s', HF_DATASET)
    except Exception as exc:
        # 备份失败不应使容器崩溃
        log.warning('备份失败（已忽略）: %s', exc)
