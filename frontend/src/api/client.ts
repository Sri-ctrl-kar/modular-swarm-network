import {
  NetworkTopology,
  SimulationStateResponse,
  DemandMapResponse,
  RouteSwitchResponse,
  OrchestrationObservationData,
  CycleAuditRecord,
  ComparisonResult,
  ScenarioDefinition,
} from '../types/api';

const API_BASE = '/api';

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const errorText = await res.text();
    let errorJson;
    try {
      errorJson = JSON.parse(errorText);
    } catch {
      // not json
    }
    const message = errorJson?.error || errorJson?.detail || errorText || `HTTP Error ${res.status}`;
    throw new Error(message);
  }
  return res.json();
}

export const api = {
  // System Health
  async getHealth(): Promise<{ status: string; gemini_mode: string; api_key_status: any; python_version: string }> {
    const res = await fetch(`${API_BASE}/health`);
    return handleResponse(res);
  },

  // City Network
  async getNetwork(): Promise<NetworkTopology> {
    const res = await fetch(`${API_BASE}/network`);
    return handleResponse(res);
  },

  // Available Scenarios
  async getScenarios(): Promise<{ scenarios: ScenarioDefinition[] }> {
    const res = await fetch(`${API_BASE}/scenarios`);
    return handleResponse(res);
  },

  // Simulation Session
  async initSimulation(params: {
    scenario_id?: string;
    pods?: number;
    passengers?: number;
    city_seed?: number;
    fleet_seed?: number;
    demand_seed?: number;
    enable_swarms?: boolean;
    enable_rebalancing?: boolean;
  }): Promise<SimulationStateResponse> {
    const res = await fetch(`${API_BASE}/simulation/init`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    });
    return handleResponse(res);
  },

  async stepSimulation(ticks: number = 1): Promise<SimulationStateResponse> {
    const res = await fetch(`${API_BASE}/simulation/step`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticks }),
    });
    return handleResponse(res);
  },

  async resetSimulation(): Promise<SimulationStateResponse> {
    const res = await fetch(`${API_BASE}/simulation/reset`, {
      method: 'POST',
    });
    return handleResponse(res);
  },

  async getState(): Promise<SimulationStateResponse> {
    const res = await fetch(`${API_BASE}/simulation/state`);
    return handleResponse(res);
  },

  async setTraffic(edge_id: string, utilization: number): Promise<any> {
    const res = await fetch(`${API_BASE}/simulation/set-traffic`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ edge_id, utilization }),
    });
    return handleResponse(res);
  },

  // Demand Heatmap
  async getDemandMap(): Promise<DemandMapResponse> {
    const res = await fetch(`${API_BASE}/demand-map`);
    return handleResponse(res);
  },

  // Congestion Rerouting Demonstration
  async getRouteSwitch(origin: string = 'north_station', destination: string = 'airport', congest_edge: string = 'E007', utilization: number = 2.0): Promise<RouteSwitchResponse> {
    const res = await fetch(`${API_BASE}/route-switch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ origin, destination, congest_edge, utilization }),
    });
    return handleResponse(res);
  },

  // Gemini Orchestrator
  async getObservation(): Promise<OrchestrationObservationData> {
    const res = await fetch(`${API_BASE}/orchestrator/observation`);
    return handleResponse(res);
  },

  async runOrchestrationCycle(advance_min?: number): Promise<{ record: CycleAuditRecord; state: SimulationStateResponse }> {
    const res = await fetch(`${API_BASE}/orchestrator/cycle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ advance_min }),
    });
    return handleResponse(res);
  },

  async validateAction(action: any): Promise<any> {
    const res = await fetch(`${API_BASE}/orchestrator/validate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    });
    return handleResponse(res);
  },

  async executeAction(action: any): Promise<any> {
    const res = await fetch(`${API_BASE}/orchestrator/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    });
    return handleResponse(res);
  },

  async getAuditLog(): Promise<any> {
    const res = await fetch(`${API_BASE}/orchestrator/audit`);
    return handleResponse(res);
  },

  // Scenario Comparison
  async getComparison(): Promise<ComparisonResult> {
    const res = await fetch(`${API_BASE}/comparison`);
    return handleResponse(res);
  },
};
