# Owner: ACTIVE
"""CLI for the Qwenwitz Agent (Hearts of Iron IV modding).

Interactive:
  python -m hoi4_agent.cli
One-shot (auto-approve optional):
  python -m hoi4_agent.cli --one-shot "add a German focus that gives 50 political power" --yes
"""

from __future__ import annotations

import argparse

from .agent import Agent
from .project import ProjectExecutor, load_project

BANNER = """
Qwenwitz Agent
-------------
The agent never guesses identifiers: it searches the vanilla index, reads
vanilla examples, validates every edit, and only writes after approval.
Type 'exit' or 'quit' to leave.
"""


def run_one_shot(agent: Agent, request: str, yes: bool = False) -> dict:
    if yes:
        agent.auto_approve = True
    print(f"\n> {request}")
    result = agent.run(request)
    print("\n" + result.get("summary", ""))
    return result


def _print_project(project, executor: ProjectExecutor) -> None:
    print(f"\nProject: {project.name}  [{project.status}]  tag={project.plan.country_tag} "
          f"feature={project.plan.feature}")
    for t in project.plan.tasks:
        status = project.task_status.get(t.id, "pending")
        print(f"  {status:10} {t.id:12} <- {', '.join(t.dependencies) or '-'}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Qwenwitz Agent CLI")
    ap.add_argument("--one-shot", help="run a single request and exit")
    ap.add_argument("--project", help="create and run a full project and exit")
    ap.add_argument("--yes", action="store_true", help="auto-approve patch application")
    ap.add_argument("--no-model", action="store_true", help="disable the optional model layer")
    args = ap.parse_args()

    agent = Agent(auto_approve=args.yes, use_model=not args.no_model)
    executor = ProjectExecutor(agent)
    current_project = None

    def create_and_run(request: str) -> None:
        nonlocal current_project
        print(f"\nCreating project: {request}")
        project = executor.create_project(request)
        current_project = project
        _print_project(project, executor)
        print("\nExecuting tasks...")
        result = executor.run(project, auto_approve=args.yes)
        print(f"\n{result['message']} | applied={result['applied']} "
              f"status={result['status']} repairs={result['stats'].get('repair_iterations', 0)}")
        _print_project(project, executor)

    if args.one_shot:
        run_one_shot(agent, args.one_shot, yes=args.yes)
        return
    if args.project:
        if args.project.lower().startswith("resume "):
            slug = args.project[7:].strip().strip('"')
            project = load_project(slug)
            if project is None:
                print(f"project not found: {slug}")
                return
            current_project = project
            _print_project(project, executor)
            result = executor.run(project, auto_approve=args.yes)
            print(f"\n{result['message']} | applied={result['applied']} status={result['status']}")
        else:
            create_and_run(args.project)
        return
    print(BANNER)
    while True:
        try:
            request = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not request or request.lower() in ("exit", "quit"):
            break
        low = request.lower()
        if low.startswith("create project"):
            create_and_run(request[len("create project"):].strip().strip('"'))
            continue
        if low.startswith("resume"):
            slug = request[len("resume"):].strip().strip('"')
            project = load_project(slug)
            if project is None:
                print(f"project not found: {slug}")
                continue
            current_project = project
            _print_project(project, executor)
            result = executor.run(project, auto_approve=args.yes)
            print(f"\n{result['message']} | applied={result['applied']} status={result['status']}")
            continue
        if current_project is not None:
            if low in ("status",):
                _print_project(current_project, executor)
                continue
            if low == "abort":
                current_project.status = "aborted"
                current_project.save()
                print("project aborted")
                continue
            if low in ("show plan", "show completed", "show pending"):
                for t in current_project.plan.tasks:
                    st = current_project.task_status.get(t.id, "pending")
                    if low.endswith("plan") or st == low.split()[-1]:
                        print(f"  {st:10} {t.id:12} {t.objective}")
                continue
        result = agent.run(request)
        print("\n" + result.get("summary", ""))


if __name__ == "__main__":
    main()
