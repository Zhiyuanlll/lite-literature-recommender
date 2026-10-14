from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path
from .service import Service
from . import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lite Recommender 2.2 — 通用本地文献推荐与评分反馈")
    parser.add_argument("--workspace", default="workspace", help="独立数据目录；默认当前目录/workspace")
    parser.add_argument("--version", action="version", version="Lite Recommender " + __version__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="初始化，不自动检索或注册定时任务")
    init.add_argument("--examples", action="store_true")
    serve = sub.add_parser("serve", help="本地网页与可选每周调度")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open", action="store_true")
    run = sub.add_parser("run", help="运行推荐；同批次重复执行复用已有结果")
    run.add_argument("--topic", action="append")
    run.add_argument("--action", choices=("weekly","preview_next","reselect","accelerate"), help="每周推荐、下周预览、重选本周推荐或评分后加速迭代")
    run.add_argument("--base-run-id", help="加速迭代所依据的已完成评分清单")
    run.add_argument("--demo", action="store_true", help="仅使用标记的合成候选，不联网")
    run.add_argument("--preview", action="store_true", help="真实检索预览，不占周配额、不改变正式推送去重记录")
    run.add_argument("--run-id")
    run.add_argument('--search-page',type=int,default=0)
    run.add_argument('--parent-run-id',help='连续分页预览的上一批次ID')
    semantic = sub.add_parser('semantic-setup',help='显式下载本地语义模型；须预先安装可选依赖')
    semantic.add_argument('--model-dir',help='工作区 _shared/models 下的模型目录')
    fb = sub.add_parser("feedback", help="评分与原因，none撤回评分")
    fb.add_argument("--topic", required=True)
    fb.add_argument("--paper", required=True)
    fb.add_argument("--rating", default="none")
    fb.add_argument("--reason", default="other")
    fb.add_argument("--note", default="")
    fb.add_argument("--tags", default="")
    fb.add_argument("--status", default="unread")
    fb.add_argument("--profile-version", help="给历史批次评分时指定该批次研究配置版本")
    export = sub.add_parser("export")
    export.add_argument("--run-id", required=True)
    export.add_argument("--format", choices=("bib", "json", "markdown"), default="bib")
    export.add_argument("--output")
    sub.add_parser("backup", help="个人私有备份，不作为公开发布包")
    download = sub.add_parser("download", help="可选下载已高评分论文的公开PDF直链")
    download.add_argument("--run-id", required=True)
    download.add_argument("--min-rating", type=int, default=4)
    migrate = sub.add_parser("import-legacy")
    migrate.add_argument("--path", required=True)
    migrate.add_argument("--topic", required=True)
    sub.add_parser("verify", help="隔离离线回归验证，不使用真实评分")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            project = Path(__file__).resolve().parent.parent
            tests = project / "tests" if (project / "tests").is_dir() else Path(__file__).resolve().parent / "_tests"
            return subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", str(tests), "-v"], cwd=project).returncode
        if args.command == "serve":
            from .web import start
            start(Path(args.workspace), port=args.port, open_browser=args.open)
            return 0
        service = Service(args.workspace)
        if args.command == "init":
            if args.examples:
                service.seed_examples()
            result = {"workspace": str(service.workspace), "topics": len(service.store.topics()), "scheduler_enabled": service.settings["schedule"]["enabled"]}
        elif args.command == "run":
            result = service.run(args.topic,args.demo,args.run_id,preview=args.preview,search_page=args.search_page,parent_run_id=args.parent_run_id,action=args.action,base_run_id=args.base_run_id)
        elif args.command == 'semantic-setup':
            result = service.setup_semantic(args.model_dir)
        elif args.command == "feedback":
            rating = None if args.rating.lower() in ("none", "null", "待评") else int(args.rating)
            result = service.feedback({"topic_id": args.topic, "paper_id": args.paper, "profile_version": args.profile_version, "rating": rating, "reason": args.reason, "note": args.note, "tags": [v.strip() for v in args.tags.split(",") if v.strip()], "read_status": args.status})
        elif args.command == "export":
            value = service.export_run(args.run_id, args.format)
            if args.output:
                target = Path(args.output)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(value, encoding="utf-8")
                result = {"output": str(target.resolve())}
            else:
                print(value)
                return 0
        elif args.command == "backup":
            result = {"backup": str(service.backup()), "private_data": True}
        elif args.command == "download":
            from .download import download_run
            if not 1 <= args.min_rating <= 5:
                raise ValueError("最低评分须为1–5")
            result = download_run(service, args.run_id, args.min_rating)
        elif args.command == "import-legacy":
            result = service.import_legacy(args.path, args.topic)
        else:
            parser.error("unknown command")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print("错误：" + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
