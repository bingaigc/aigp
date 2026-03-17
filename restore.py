"""
restore.py — HF Hub 数据恢复脚本

在容器启动时运行，将 Hugging Face Dataset 仓库中保存的
/root/.openclaw 快照恢复到本地，使每次重新部署都能继承上次的
配置、对话历史和 Agent 状态。

环境变量：
  HF_DATASET  — HF Dataset 仓库 ID，如 "your-user/openclaw-backup"
  HF_TOKEN    — Hugging Face 访问令牌（需要 write 权限）

若两个变量均未设置，脚本静默跳过（不影响正常启动流程）。
"""
import os
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [restore] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('restore')

HF_DATASET  = os.environ.get('HF_DATASET', '').strip()
HF_TOKEN    = os.environ.get('HF_TOKEN',   '').strip()
LOCAL_DIR   = '/root/.openclaw'

if not HF_DATASET or not HF_TOKEN:
    log.warning(
        '⚠️  HF_DATASET 或 HF_TOKEN 未设置，跳过恢复步骤。\n'
        '   首次部署时这是正常的；若已配置凭据，请检查环境变量是否正确注入。'
    )
else:
    try:
        from huggingface_hub import snapshot_download
        log.info('开始从 HF Hub 恢复数据: %s → %s', HF_DATASET, LOCAL_DIR)
        os.makedirs(LOCAL_DIR, exist_ok=True)
        snapshot_download(
            repo_id=HF_DATASET,
            repo_type='dataset',
            local_dir=LOCAL_DIR,
            token=HF_TOKEN,
            ignore_patterns=['*.log', 'tmp/*'],
        )
        log.info('✅ 数据恢复完成。')
    except Exception as exc:
        # 恢复失败不应阻塞容器启动（首次部署时仓库可能为空）
        log.warning('恢复失败（跳过，继续启动）: %s', exc)
