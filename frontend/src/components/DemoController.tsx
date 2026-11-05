import React, { useEffect, useState } from 'react';
import { Film, CheckCircle, ChevronRight, X, Play } from 'lucide-react';

export interface DemoStep {
  seconds: number;
  phase: string;
  description: string;
  action?: () => Promise<void> | void;
}

interface DemoControllerProps {
  isRunning: boolean;
  onStop: () => void;
  currentElapsed: number;
  currentPhase: string;
}

export const DemoController: React.FC<DemoControllerProps> = ({
  isRunning,
  onStop,
  currentElapsed,
  currentPhase,
}) => {
  if (!isRunning) return null;

  const totalDuration = 40;
  const progressPct = Math.min(100, (currentElapsed / totalDuration) * 100);

  return (
    <div className="fixed top-14 left-1/2 -translate-x-1/2 z-40 w-full max-w-2xl px-4 animate-in slide-in-from-top-4 duration-300 font-mono select-none">
      <div className="bg-[#0b0f17]/95 border border-cyan-500/60 rounded-lg p-3 shadow-2xl backdrop-blur-md flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full bg-cyan-400 animate-ping"></span>
            <span className="text-xs font-bold text-cyan-300 tracking-wider uppercase flex items-center gap-1.5">
              <Film className="w-3.5 h-3.5" />
              Competition Judge Showcase
            </span>
          </div>

          <div className="flex items-center gap-3">
            <span className="text-xs text-slate-400">
              {currentElapsed.toFixed(1)}s / {totalDuration}s
            </span>
            <button
              onClick={onStop}
              className="p-1 rounded hover:bg-slate-800 text-slate-400 hover:text-slate-200 transition-colors"
              title="Stop Demo"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Progress Bar */}
        <div className="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
          <div
            className="bg-gradient-to-r from-cyan-500 to-emerald-400 h-full transition-all duration-300"
            style={{ width: `${progressPct}%` }}
          ></div>
        </div>

        {/* Current Demo Phase */}
        <div className="flex items-center justify-between text-xs pt-0.5">
          <span className="text-slate-200 font-sans font-medium flex items-center gap-1.5">
            <ChevronRight className="w-3.5 h-3.5 text-cyan-400 shrink-0" />
            {currentPhase}
          </span>
          <span className="text-[10px] text-cyan-400 uppercase tracking-widest font-bold">
            Live Engine Active
          </span>
        </div>
      </div>
    </div>
  );
};
