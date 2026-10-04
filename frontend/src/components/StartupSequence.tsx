import React, { useEffect, useState } from 'react';
import { Activity, CheckCircle2, Terminal } from 'lucide-react';

interface StartupSequenceProps {
  onComplete: () => void;
}

export const StartupSequence: React.FC<StartupSequenceProps> = ({ onComplete }) => {
  const steps = [
    'INITIALIZING NETWORK TOPOLOGY',
    'LOADING SYNTHETIC DEMAND MODEL',
    'INITIALIZING AUTONOMOUS FLEET',
    'INITIALIZING SWARM ENGINE',
    'CONNECTING GEMINI ORCHESTRATOR',
    'SIMULATION ENGINE READY',
  ];

  const [currentStepIndex, setCurrentStepIndex] = useState(0);

  useEffect(() => {
    if (currentStepIndex < steps.length - 1) {
      const timer = setTimeout(() => {
        setCurrentStepIndex((prev) => prev + 1);
      }, 350);
      return () => clearTimeout(timer);
    } else {
      const endTimer = setTimeout(() => {
        onComplete();
      }, 500);
      return () => clearTimeout(endTimer);
    }
  }, [currentStepIndex, steps.length, onComplete]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#07090e] font-mono select-none">
      <div className="max-w-md w-full p-6 flex flex-col gap-5 border border-slate-800 rounded-lg bg-[#0b0f17] shadow-2xl">
        <div className="flex items-center gap-2.5 border-b border-slate-800 pb-3">
          <Terminal className="w-5 h-5 text-cyan-400" />
          <h2 className="text-xs font-bold text-slate-200 uppercase tracking-widest">
            Modular Swarm Command Center
          </h2>
        </div>

        <div className="flex flex-col gap-2.5">
          {steps.map((step, idx) => {
            const isDone = idx < currentStepIndex;
            const isCurrent = idx === currentStepIndex;

            if (idx > currentStepIndex + 1) return null;

            return (
              <div
                key={idx}
                className={`flex items-center gap-2.5 text-xs transition-opacity duration-200 ${
                  isDone
                    ? 'text-emerald-400'
                    : isCurrent
                    ? 'text-cyan-300 font-bold'
                    : 'text-slate-600 opacity-40'
                }`}
              >
                {isDone ? (
                  <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                ) : isCurrent ? (
                  <Activity className="w-4 h-4 text-cyan-400 animate-spin shrink-0" />
                ) : (
                  <span className="w-4 h-4 rounded-full border border-slate-700 shrink-0"></span>
                )}
                <span>{step}</span>
              </div>
            );
          })}
        </div>

        <div className="w-full bg-slate-900 h-1 rounded-full overflow-hidden mt-1">
          <div
            className="bg-cyan-400 h-full transition-all duration-300 ease-out"
            style={{ width: `${((currentStepIndex + 1) / steps.length) * 100}%` }}
          ></div>
        </div>
      </div>
    </div>
  );
};
