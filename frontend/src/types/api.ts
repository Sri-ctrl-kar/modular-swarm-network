/**
 * Type definitions mirroring the backend M1-M6 deterministic engine.
 */

export interface NetworkNode {
  id?: string;
  node_id: string;
  name: string;
  latitude: float;
  longitude: float;
  x?: float;
  y?: float;
  node_type: 'intersection' | 'station' | 'terminal';
}

export type float = number;

export interface NetworkEdge {
  id?: string;
  edge_id: string;
  source: string;
  target?: string;
  destination: string;
  distance_km: number;
  base_travel_time_min: number;
  capacity?: number;
  capacity_vehicles_per_hour: number;
  road_type: 'local' | 'arterial' | 'trunk';
  current_vehicle_count: number;
  current_travel_time_min: number;
  utilization: number;
}

export interface NetworkTopology {
  nodes: NetworkNode[];
  edges: NetworkEdge[];
  bounds: {
    min_lat: number;
    max_lat: number;
    min_lon: number;
    max_lon: number;
  };
  node_count: number;
  edge_count: number;
}

export interface PodState {
  pod_id: string;
  current_node_id: string;
  current_edge_id: string | null;
  status: 'idle' | 'assigned' | 'traveling' | 'arrived' | 'charging';
  battery_percent: number;
  occupied_seats: number;
  trip_kind: 'passenger' | 'repositioning';
  edge_progress: number; // 0.0 to 1.0 along current edge
  swarm_id: string | null;
  route_edges: string[];
  route_index: number;
}

export interface SwarmState {
  swarm_id: string;
  status: 'forming' | 'active' | 'splitting' | 'completed';
  member_pod_ids: string[];
  origin_node_id: string;
  divergence_node_id: string;
  corridor_edges: string[];
  current_edge_id: string | null;
  shared_distance_km: number;
  corridor_distance_km: number;
}

export interface EdgeCongestionState {
  vehicle_count: number;
  capacity: number;
  utilization: number;
  current_travel_time_min: number;
  congestion_multiplier: number;
  is_overloaded: boolean;
}

export interface SimulationMetrics {
  time_min: number;
  tick_index: number;
  is_finished: boolean;
  total_pods: number;
  active_pods: number;
  idle_pods: number;
  charging_pods: number;
  active_swarms: number;
  total_swarms_formed: number;
  trips_served: number;
  unserved_trips: number;
  average_wait_min: number | null;
  average_completion_time_min: number | null;
  total_energy_kwh: number;
  pod_distance_km: number;
  deadhead_distance_km: number;
  deadhead_ratio: number;
  passenger_distance_km: number;
  road_occupancy_equiv_km: number;
  road_occupancy_saved_equiv_km: number;
  completed_repositions: number;
  final_total_deficit: number;
}

export interface SimulationEvent {
  id: string;
  time_min: number;
  kind: 'SIMULATION_INIT' | 'SCENARIO_LOADED' | 'SWARM_FORMATION' | 'SWARM_SPLIT' | 'CONGESTION' | 'ROUTE_CHANGE' | 'REBALANCING' | 'ORCHESTRATOR' | 'TRAFFIC_INJECTED';
  message: string;
  details?: Record<string, any>;
}

export interface SimulationStateResponse {
  time_min: number;
  tick_index: number;
  is_finished: boolean;
  pods: PodState[];
  swarms: SwarmState[];
  edges: Record<string, EdgeCongestionState>;
  metrics: SimulationMetrics;
  new_events: SimulationEvent[];
  provider_type: 'mock' | 'live';
}

export interface NodeDemandForecast {
  node_id: string;
  expected_trips: number;
  expected_passengers: number;
  required_pods: number;
  available_pods: number;
  net_balance: number;
  deficit: number;
  surplus: number;
  is_deficit: boolean;
  is_surplus: boolean;
}

export interface DemandMapResponse {
  time_min: number;
  forecast_horizon_min: number;
  total_forecast_demand: number;
  total_deficit: number;
  total_surplus: number;
  deficit_nodes: string[];
  surplus_nodes: string[];
  nodes: Record<string, NodeDemandForecast>;
  top_od_flows?: Array<{
    origin: string;
    destination: string;
    trips: number;
  }>;
}

export interface RouteSwitchResponse {
  origin: string;
  destination: string;
  congested_edge: string;
  congested_utilization: number;
  route_changed: boolean;
  before_route: {
    origin: string;
    destination: string;
    node_ids: string[];
    edge_ids: string[];
    total_distance_km: number;
    total_travel_time_min: number;
    free_flow_travel_time_min: number;
  };
  after_route: {
    origin: string;
    destination: string;
    node_ids: string[];
    edge_ids: string[];
    total_distance_km: number;
    total_travel_time_min: number;
    free_flow_travel_time_min: number;
  };
}

export interface OrchestrationObservationData {
  time_min: number;
  fingerprint: string;
  headline: string;
  network: Record<string, any>;
  demand: {
    deficit_node_count: number;
    surplus_node_count: number;
    top_surplus_nodes?: any[];
    [key: string]: any;
  };
  fleet: {
    idle_eligible_pods: number;
    average_battery_percent: number;
    minimum_battery_percent: number;
    battery_distribution_percent_buckets: Record<string, number>;
    [key: string]: any;
  };
  swarm: {
    active_swarm_count: number;
    active_corridors?: any[];
    [key: string]: any;
  };
  metrics: Record<string, any>;
  provenance: string;
}

export interface CycleAuditRecord {
  cycle_id: string;
  time_min: number;
  observation_fingerprint: string;
  proposal: {
    action_type: 'REQUEST_REBALANCING' | 'RUN_SIMULATION' | 'COMPARE_SCENARIOS' | 'NO_ACTION';
    parameters: Record<string, any>;
    reason: string;
    expected_effect: string;
    confidence: number;
    observation_fingerprint: string;
  } | null;
  verdict: 'APPROVED' | 'REJECTED';
  verdict_detail?: string;
  reason_code?: string | null;
  checks_passed: number;
  total_checks: number;
  execution_status?: string | null;
  execution_detail?: string | null;
  result?: {
    advance_min?: number;
    deficit_before?: number;
    deficit_after?: number;
    repositionings_dispatched?: number;
    [key: string]: any;
  };
}

export interface ComparisonResult {
  horizon_min: number;
  baseline: Record<string, any>;
  swarm: Record<string, any>;
  adaptive: Record<string, any>;
  impact_summary: {
    road_space_saved_equiv_km: number;
    trips_served_gain: number;
    wait_time_reduction_pct: number;
    deadhead_pct: number;
  };
  provenance: string;
  scenario_potential_note: string;
}

export interface ScenarioDefinition {
  id: string;
  name: string;
  description: string;
  type: 'standard' | 'evaluation';
  pods?: number;
  passengers?: number;
  expected_consideration?: string;
}
