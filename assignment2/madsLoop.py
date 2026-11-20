import argparse
from src.agentic_loop import run_agent

def main():
    print("This is CS2680 Assignment 2 running")
    parser = argparse.ArgumentParser()
    parser.add_argument("-p",required=True)
    parser.add_argument("--log",action="store_true")
    parser.add_argument("workdir")
    args = parser.parse_args()

    print("Problem:", args.p)
    print("Logging:", args.log)
    print("Working directory:", args.workdir)
    run_agent(args.p, args.workdir, args.log)



if __name__ == "__main__":
    main()