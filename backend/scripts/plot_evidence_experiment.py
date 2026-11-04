"""Render the published aggregate results (requires optional matplotlib)."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import numpy as np


def main():
    root=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results",type=Path,default=root/"docs/results/evidence-selection-v1.json")
    parser.add_argument("--out",type=Path,default=root/"docs/assets/evidence-selection-results")
    args=parser.parse_args()
    data=json.loads(args.results.read_text("utf-8"))["summaries"]
    plt.rcParams.update({"font.family":"DejaVu Sans","font.size":11,"axes.spines.top":False,
        "axes.spines.right":False,"axes.spines.left":False,"axes.spines.bottom":False,
        "axes.labelcolor":"#34445d","xtick.color":"#51617a","ytick.color":"#51617a","text.color":"#172a46"})
    fig,axes=plt.subplots(1,2,figsize=(12.8,6),facecolor="#f5f8fc")
    styles=[("prefix","Prefix allocation","#4866bf"),("selected","Model selection","#c88a3d"),("lexical","Local BM25 selection","#138e86")]
    x=np.arange(2);width=.23
    for ax in axes:
        ax.set_facecolor("#f5f8fc");ax.set_axisbelow(True);ax.grid(axis="y",color="#dde4ee",linewidth=.8)
        ax.tick_params(length=0);ax.set_xticks(x,["3,500 characters","9,000 characters"])
    for index,(policy,label,color) in enumerate(styles):
        values=[data[f"{policy}-{cap}"]["answer_token_f1"]*100 for cap in (3500,9000)]
        costs=[data[f"{policy}-{cap}"]["total_pipeline_tokens"] for cap in (3500,9000)]
        for ax,heights,fmt in ((axes[0],values,"%.2f"),(axes[1],costs,"%.0f")):
            bars=ax.bar(x+(index-1)*width,heights,width*.92,color=color,label=label,zorder=3)
            ax.bar_label(bars,fmt=fmt,padding=5,fontsize=10,color="#24364f")
    axes[0].set_title("Answer token F1",loc="left",fontweight="bold",pad=22)
    axes[0].set_ylim(0,50);axes[0].set_ylabel("Score (0-100)")
    axes[1].set_title("Selection + final-answer cost",loc="left",fontweight="bold",pad=22)
    axes[1].set_ylim(0,3650);axes[1].set_ylabel("Mean API tokens per task")
    axes[1].yaxis.set_major_formatter(FuncFormatter(lambda y,_:f"{y:,.0f}"))
    fig.text(.06,.94,"Evidence selection under a limited context",fontsize=21,fontweight="bold")
    fig.text(.06,.886,"QASPER development set  /  20 fixed-read tasks  /  2 answer repeats  /  Qwen3.5-27B",fontsize=11,color="#5c6d83")
    handles,labels=axes[0].get_legend_handles_labels()
    fig.legend(handles,labels,loc="lower left",bbox_to_anchor=(.05,.09),ncol=3,frameon=False)
    fig.text(.06,.064,"Exploratory results; same historical reads, no new retrieval. F1 is a lexical metric, not factual accuracy.",fontsize=10,color="#5c6d83")
    fig.text(.06,.03,"Model-selection cost is included. Full-read diagnostic is reported separately. Held-out testing remains pending.",fontsize=10,color="#5c6d83")
    fig.subplots_adjust(left=.08,right=.97,bottom=.24,top=.76,wspace=.28)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    for suffix in (".png",".svg"):
        fig.savefig(args.out.with_suffix(suffix),dpi=170,facecolor=fig.get_facecolor())
    svg=args.out.with_suffix(".svg")
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text("utf-8").splitlines())+"\n",encoding="utf-8")
    plt.close(fig)
    print(str(args.out.with_suffix(".png")))


if __name__=="__main__":main()
