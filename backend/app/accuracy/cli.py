"""Train the dedicated accuracy profile without changing existing model activation."""
import argparse
from pathlib import Path

from .training import train


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["train"])
    parser.add_argument("--ticks",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--profile",choices=["compact_v1", "technical_v2"],default="technical_v2")
    parser.add_argument("--no-activate",action="store_true",help="Save the trained report without switching the live profile")
    args=parser.parse_args()
    train(args.ticks,args.output,feature_profile=args.profile,activate=not args.no_activate)


if __name__=="__main__":
    main()
