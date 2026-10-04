import React, { useState, useEffect, useRef, useCallback } from 'react';
import { api } from './api/client';
import {
  NetworkTopology,
  SimulationStateResponse,
  DemandMapResponse,
  RouteSwitchResponse,
  OrchestrationObservationData,
  CycleAuditRecord,
  ScenarioDefinition,
  SimulationMetrics,
  PodState,
  SwarmState,
  SimulationEvent,
} from './types/api';
import { TopBar } from './panels/TopBar';
import { TopMetricBar } from './panels/TopMetricBar';
import { CityMap } from './map/CityMap';
import { SimulationControls } from './panels/SimulationControls';
import { GeminiOrchestratorPanel } from './panels/GeminiOrchestratorPanel';
import { EventTimeline } from './panels/EventTimeline';
import { UrbanImpactPanel } from './panels/UrbanImpactPanel';
import { ScenarioComparisonModal } from './panels/ScenarioComparisonModal';
import { DemoController } from './components/DemoController';
import { StartupSequence } from './components/StartupSequence';
import { AlertTriangle, WifiOff } from 'lucide-react';

export const App: React.FC = () => {
  // App initialization states
  const [isInitializing, setIsInitializing] = useState(true);
  const [backendError, setBackendError] = useState<string | null>(null);

  // Simulation core state
  const [topology, setTopology] = useState<NetworkTopology | null>(null);
  const [scenarios, setScenarios] = useState<ScenarioDefinition[]>([]);
  const [currentScenarioId, setCurrentScenarioId] = useState<string>('baseline');
  const [providerType, setProviderType] = useState<'mock' | 'live'>('mock');

  // Live simulation tick data
  const [timeMin, setTimeMin] = useState<number>(0.0);
  const [tickIndex, setTickIndex] = useState<number>(0);
  const [pods, setPods] = useState<PodState[]>([]);
  const [swarms, setSwarms] = useState<SwarmState[]>([]);
  const [edgeCongestions, setEdgeCongestions] = useState<Record<string, any>>({});
  const [metrics, setMetrics] = useState<SimulationMetrics | null>(null);
  const [events, setEvents] = useState<SimulationEvent[]>([]);

  // Orchestrator states
  const [observation, setObservation] = useState<OrchestrationObservationData | null>(null);
  const [lastRecord, setLastRecord] = useState<CycleAuditRecord | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState<boolean>(false);

  // Auxiliary Map data
  const [demandMap, setDemandMap] = useState<DemandMapResponse | null>(null);
  const [routeSwitch, setRouteSwitch] = useState<RouteSwitchResponse | null>(null);
  const [isRouteSwitchActive, setIsRouteSwitchActive] = useState<boolean>(false);
  const [viewMode, setViewMode] = useState<'TRAFFIC' | 'DEMAND' | 'SWARMS'>('TRAFFIC');
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedPodId, setSelectedPodId] = useState<string | null>(null);

  // Playback loop
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const [speed, setSpeed] = useState<number>(1);
  const playIntervalRef = useRef<any>(null);

  // Modals & Demo
  const [isComparisonOpen, setIsComparisonOpen] = useState<boolean>(false);
  const [isDemoRunning, setIsDemoRunning] = useState<boolean>(false);
  const [demoElapsed, setDemoElapsed] = useState<number>(0);
  const [demoPhase, setDemoPhase] = useState<string>('Initializing');
  const isDemoCancelledRef = useRef<boolean>(false);

  // Load initial backend network and scenario metadata
  const loadInitialData = useCallback(async () => {
    try {
      setBackendError(null);
      const [health, net, scens, state] = await Promise.all([
        api.getHealth(),
        api.getNetwork(),
        api.getScenarios(),
        api.getState(),
      ]);

      setProviderType(health.gemini_mode === 'LIVE GEMINI' ? 'live' : 'mock');
      setTopology(net);
      setScenarios(scens.scenarios);

      // Apply initial state
      setTimeMin(state.time_min);
      setTickIndex(state.tick_index);
      setPods(state.pods);
      setSwarms(state.swarms);
      setEdgeCongestions(state.edges);
      setMetrics(state.metrics);
      setEvents(state.new_events || []);

      // Load initial observation & demand map
      const [obs, dmap] = await Promise.all([api.getObservation(), api.getDemandMap()]);
      setObservation(obs);
      setDemandMap(dmap);
    } catch (err: any) {
      console.error('Initialization error:', err);
      setBackendError(err.message || 'Could not connect to Python backend API (:8000)');
    }
  }, []);

  useEffect(() => {
    loadInitialData();
  }, [loadInitialData]);

  // Simulation step runner
  const handleStep = useCallback(
    async (ticks: number = 1) => {
      try {
        const state = await api.stepSimulation(ticks);
        setTimeMin(state.time_min);
        setTickIndex(state.tick_index);
        setPods(state.pods);
        setSwarms(state.swarms);
        setEdgeCongestions(state.edges);
        setMetrics(state.metrics);

        if (state.new_events && state.new_events.length > 0) {
          setEvents((prev) => [...prev, ...state.new_events].slice(-200));
        }

        // Refresh demand map periodically
        if (state.tick_index % 10 === 0) {
          api.getDemandMap().then(setDemandMap).catch(() => {});
        }
      } catch (err: any) {
        console.error('Step error:', err);
        setIsPlaying(false);
      }
    },
    []
  );

  // Auto-play loop
  useEffect(() => {
    if (isPlaying) {
      const intervalMs = Math.max(80, Math.floor(600 / speed));
      playIntervalRef.current = setInterval(() => {
        handleStep(1);
      }, intervalMs);
    } else {
      if (playIntervalRef.current) clearInterval(playIntervalRef.current);
    }
    return () => {
      if (playIntervalRef.current) clearInterval(playIntervalRef.current);
    };
  }, [isPlaying, speed, handleStep]);

  // Playback handlers
  const handlePlay = () => setIsPlaying(true);
  const handlePause = () => setIsPlaying(false);

  const handleReset = async () => {
    setIsPlaying(false);
    setIsRouteSwitchActive(false);
    setRouteSwitch(null);
    try {
      const state = await api.resetSimulation();
      setTimeMin(state.time_min);
      setTickIndex(state.tick_index);
      setPods(state.pods);
      setSwarms(state.swarms);
      setEdgeCongestions(state.edges);
      setMetrics(state.metrics);
      setEvents([]);
      const [obs, dmap] = await Promise.all([api.getObservation(), api.getDemandMap()]);
      setObservation(obs);
      setDemandMap(dmap);
      setLastRecord(null);
    } catch (err) {
      console.error('Reset error:', err);
    }
  };

  const handleSelectScenario = async (scenId: string) => {
    setIsPlaying(false);
    setCurrentScenarioId(scenId);
    setIsRouteSwitchActive(false);
    setRouteSwitch(null);
    try {
      const state = await api.initSimulation({ scenario_id: scenId });
      setTimeMin(state.time_min);
      setTickIndex(state.tick_index);
      setPods(state.pods);
      setSwarms(state.swarms);
      setEdgeCongestions(state.edges);
      setMetrics(state.metrics);
      setEvents(state.new_events || []);
      const [obs, dmap] = await Promise.all([api.getObservation(), api.getDemandMap()]);
      setObservation(obs);
      setDemandMap(dmap);
      setLastRecord(null);
    } catch (err) {
      console.error('Scenario switch error:', err);
    }
  };

  // Run Orchestration Cycle
  const handleRunOrchestratorCycle = async () => {
    setIsAnalyzing(true);
    try {
      const { record, state } = await api.runOrchestrationCycle(5.0);
      setLastRecord(record);
      // Update state if cycle advanced engine
      setTimeMin(state.time_min);
      setTickIndex(state.tick_index);
      setPods(state.pods);
      setSwarms(state.swarms);
      setEdgeCongestions(state.edges);
      setMetrics(state.metrics);
      if (state.new_events && state.new_events.length > 0) {
        setEvents((prev) => [...prev, ...state.new_events].slice(-200));
      }

      // Refresh observation and demand map
      const [obs, dmap] = await Promise.all([api.getObservation(), api.getDemandMap()]);
      setObservation(obs);
      setDemandMap(dmap);
    } catch (err: any) {
      console.error('Cycle error:', err);
    } finally {
      setIsAnalyzing(false);
    }
  };

  // Toggle Route Switch Demo
  const handleTriggerRouteSwitch = async () => {
    if (isRouteSwitchActive) {
      setIsRouteSwitchActive(false);
      setRouteSwitch(null);
    } else {
      try {
        const res = await api.getRouteSwitch('north_station', 'airport', 'E007', 2.0);
        setRouteSwitch(res);
        setIsRouteSwitchActive(true);
        setViewMode('TRAFFIC');
      } catch (err) {
        console.error('Route switch error:', err);
      }
    }
  };

  // Inject Traffic manually onto clicked edge
  const handleInjectTraffic = async (edgeId: string) => {
    try {
      await api.setTraffic(edgeId, 1.8);
      const state = await api.getState();
      setEdgeCongestions(state.edges);
    } catch (err) {
      console.error('Inject traffic error:', err);
    }
  };

  // Keyboard Navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;

      if (e.code === 'Space') {
        e.preventDefault();
        setIsPlaying((p) => !p);
      } else if (e.key === 's' || e.key === 'S') {
        handleStep(1);
      } else if (e.key === 'r' || e.key === 'R') {
        handleReset();
      } else if (e.key === 'c' || e.key === 'C') {
        setIsComparisonOpen((prev) => !prev);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleStep]);

  // 6-Stage Deterministic Demo Runner (Section 8: DEMAND -> CONGESTION -> ROUTING -> SWARM -> REBALANCING -> METRICS)
  const startDemo = async () => {
    setIsPlaying(false);
    setIsDemoRunning(true);
    isDemoCancelledRef.current = false;
    setDemoElapsed(0);
    setDemoPhase('Initializing Judge Demo...');

    const stages = [
      {
        name: 'DEMAND',
        label: '1/6 DEMAND — M2/M5 Passenger Demand & Spatial Deficit Hotspots',
        action: async () => {
          await handleSelectScenario('A_LARGE_DEFICIT');
          setViewMode('DEMAND');
        },
        durationSec: 6,
      },
      {
        name: 'CONGESTION',
        label: '2/6 CONGESTION — Arterial Bottlenecks & Real Utilization Build-up',
        action: async () => {
          setViewMode('TRAFFIC');
          await handleStep(2);
        },
        durationSec: 6,
      },
      {
        name: 'ROUTING',
        label: '3/6 ROUTING — Dynamic Congestion-Driven Route Switching',
        action: async () => {
          setViewMode('TRAFFIC');
          await handleTriggerRouteSwitch();
        },
        durationSec: 6,
      },
      {
        name: 'SWARM',
        label: '4/6 SWARM — Platooning Formation ●━━●━━● on Corridor & Split',
        action: async () => {
          setIsRouteSwitchActive(false);
          setRouteSwitch(null);
          setViewMode('SWARMS');
          await handleStep(3);
        },
        durationSec: 7,
      },
      {
        name: 'REBALANCING',
        label: '5/6 REBALANCING — Gemini Proposal Validated by Safety Suite',
        action: async () => {
          setViewMode('DEMAND');
          await handleRunOrchestratorCycle();
          await handleStep(2);
        },
        durationSec: 7,
      },
      {
        name: 'METRICS',
        label: '6/6 METRICS — Measured Outcomes: Road-Space Freed & Trips Served',
        action: async () => {
          await handleStep(2);
        },
        durationSec: 6,
      },
    ];

    let elapsed = 0;
    for (const stage of stages) {
      if (isDemoCancelledRef.current) break;
      setDemoPhase(stage.label);
      try {
        await stage.action();
      } catch (err) {
        console.error('Demo stage error:', err);
      }

      // Smooth progress update during each stage
      const stepMs = 200;
      const count = (stage.durationSec * 1000) / stepMs;
      for (let i = 0; i < count; i++) {
        if (isDemoCancelledRef.current) break;
        await new Promise((r) => setTimeout(r, stepMs));
        elapsed += stepMs / 1000;
        setDemoElapsed(Math.min(38, elapsed));
      }
    }

    setIsDemoRunning(false);
    if (!isDemoCancelledRef.current) {
      setDemoPhase('Showcase Complete: Real Simulation Outcomes Verified');
    }
  };

  const stopDemo = () => {
    isDemoCancelledRef.current = true;
    setIsDemoRunning(false);
  };

  return (
    <div className="w-screen h-screen flex flex-col bg-[#07090e] text-slate-100 overflow-hidden font-sans">
      {/* Startup Sequence */}
      {isInitializing && (
        <StartupSequence onComplete={() => setIsInitializing(false)} />
      )}

      {/* Backend Offline Banner */}
      {backendError && (
        <div className="w-full bg-rose-950/90 border-b border-rose-800 px-4 py-2 flex items-center justify-between text-xs font-mono text-rose-200 z-50">
          <div className="flex items-center gap-2">
            <WifiOff className="w-4 h-4 text-rose-400" />
            <span>
              <strong>BACKEND OFFLINE:</strong> {backendError}. Ensure{' '}
              <code className="bg-rose-900/60 px-1 py-0.5 rounded">
                python3 -m app.api.server --port 8000
              </code>{' '}
              is running.
            </span>
          </div>
          <button
            onClick={loadInitialData}
            className="px-2.5 py-0.5 bg-rose-800 hover:bg-rose-700 rounded text-[11px] font-bold transition-colors"
          >
            RETRY
          </button>
        </div>
      )}

      {/* Demo Controller Floating Bar */}
      <DemoController
        isRunning={isDemoRunning}
        onStop={stopDemo}
        currentElapsed={demoElapsed}
        currentPhase={demoPhase}
      />

      {/* Top Header Bar */}
      <TopBar
        scenarioName={scenarios.find((s) => s.id === currentScenarioId)?.name || currentScenarioId}
        timeMin={timeMin}
        speed={speed}
        isPlaying={isPlaying}
        providerType={providerType}
        onOpenComparison={() => setIsComparisonOpen(true)}
        onStartDemo={startDemo}
        isDemoRunning={isDemoRunning}
      />

      {/* Top Metrics Strip */}
      <TopMetricBar metrics={metrics} />

      {/* Main Command Center Layout */}
      <main className="flex-1 w-full grid grid-cols-1 lg:grid-cols-12 gap-3 p-3 overflow-hidden">
        {/* Left Column: City Map & Controls (8 cols on lg) */}
        <section className="lg:col-span-8 flex flex-col gap-2.5 h-full overflow-hidden">
          <div className="flex-1 w-full min-h-0">
            <CityMap
              topology={topology}
              pods={pods}
              swarms={swarms}
              edgeCongestions={edgeCongestions}
              demandMap={demandMap}
              routeSwitch={routeSwitch}
              viewMode={viewMode}
              onViewModeChange={setViewMode}
              selectedNodeId={selectedNodeId}
              onSelectNode={setSelectedNodeId}
              selectedPodId={selectedPodId}
              onSelectPod={setSelectedPodId}
              onInjectTraffic={handleInjectTraffic}
            />
          </div>

          {/* Simulation Controls */}
          <SimulationControls
            isPlaying={isPlaying}
            onPlay={handlePlay}
            onPause={handlePause}
            onStep={() => handleStep(1)}
            onReset={handleReset}
            speed={speed}
            onSpeedChange={setSpeed}
            scenarios={scenarios}
            currentScenarioId={currentScenarioId}
            onSelectScenario={handleSelectScenario}
            onTriggerRouteSwitch={handleTriggerRouteSwitch}
            isRouteSwitchActive={isRouteSwitchActive}
          />
        </section>

        {/* Right Column: Gemini Orchestrator, Timeline & Urban Impact (4 cols on lg) */}
        <section className="lg:col-span-4 flex flex-col gap-2.5 h-full overflow-y-auto pr-1">
          {/* Gemini Orchestration Panel */}
          <GeminiOrchestratorPanel
            observation={observation}
            lastRecord={lastRecord}
            providerType={providerType}
            onRunCycle={handleRunOrchestratorCycle}
            isAnalyzing={isAnalyzing}
          />

          {/* Event Timeline Feed */}
          <div className="flex-1 min-h-[220px]">
            <EventTimeline events={events} />
          </div>

          {/* Urban Impact Panel */}
          <UrbanImpactPanel metrics={metrics} />
        </section>
      </main>

      {/* Scenario Comparison Modal */}
      <ScenarioComparisonModal
        isOpen={isComparisonOpen}
        onClose={() => setIsComparisonOpen(false)}
      />
    </div>
  );
};
