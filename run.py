#!/usr/bin/env python3
"""Policy monitoring agent entry point.

Usage:
  python run.py                          # 오늘 날짜로 일일 수집 실행
  python run.py run --date 2026-07-27   # 특정 날짜 실행
  python run.py search "중소벤처기업부"   # 아카이브 검색
  python run.py search "반도체" --committee science --start 2026-07-01
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.orchestrator import Orchestrator


def main():
    parser = argparse.ArgumentParser(
        description='소관분야 정책·예산·입법 데일리 모니터링 에이전트'
    )
    subparsers = parser.add_subparsers(dest='command')

    # run
    run_p = subparsers.add_parser('run', help='일일 수집 실행')
    run_p.add_argument('--date', help='대상 날짜 YYYY-MM-DD (기본: 오늘)')

    # search
    srch_p = subparsers.add_parser('search', help='아카이브 검색')
    srch_p.add_argument('query', help='검색어')
    srch_p.add_argument('--ministry',  help='부처 필터 (예: 금융위원회)')
    srch_p.add_argument('--committee', help='위원회 필터 (industry/science/agriculture/politics)')
    srch_p.add_argument('--start',     help='시작 날짜 YYYY-MM-DD')
    srch_p.add_argument('--end',       help='종료 날짜 YYYY-MM-DD')

    args = parser.parse_args()
    orch = Orchestrator()

    if args.command is None or args.command == 'run':
        date = getattr(args, 'date', None)
        dashboard = orch.run(date=date)
        print(f"\n대시보드 생성 완료: {dashboard}")
        print("브라우저에서 파일을 열거나 더블클릭하면 확인할 수 있습니다.")

    elif args.command == 'search':
        results = orch.search(
            args.query,
            ministry=args.ministry,
            committee=args.committee,
            start_date=args.start,
            end_date=args.end,
        )
        if not results:
            print("검색 결과가 없습니다.")
            return

        print(f"\n검색 결과: {len(results)}건\n" + "=" * 60)
        for item in results:
            imp = item.get('importance', '?')
            print(f"[{imp}] [{item.get('date', '?')}] {item.get('title', '?')}")
            print(f"      출처: {item.get('source', '?')}")
            if item.get('url'):
                print(f"      링크: {item['url']}")
            if item.get('summary'):
                print(f"      {item['summary'][:100]}")
            print()


if __name__ == '__main__':
    main()
