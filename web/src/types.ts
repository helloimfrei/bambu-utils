export interface RunStatus {
  state: string;
  active: boolean;
  paused: boolean;
  name: string;
  file: string;
  progress: number;
  remaining_minutes: number;
  layer: number;
  total_layers: number;
  stage: string | null;
  speed_level: number;
  speed_percent: number;
  error_code: number;
}

export interface TemperatureStatus {
  current: number | null;
  target: number | null;
}

export interface AmsTrayStatus {
  id: number;
  unit_id: number;
  slot_id: number;
  present: boolean;
  active: boolean;
  target: boolean;
  filament_type: string | null;
  subtype: string | null;
  color: string | null;
  profile_id: string | null;
  remaining_percent: number | null;
  pressure_advance: number | null;
}

export interface PrinterStatus {
  connected: boolean;
  updated_at: string | null;
  connection_error: string | null;
  run: RunStatus;
  temperatures: {
    nozzle: TemperatureStatus;
    bed: TemperatureStatus;
    chamber: TemperatureStatus | null;
  };
  fans: {
    part: number | null;
    heatbreak: number | null;
    auxiliary: number | null;
    chamber: number | null;
  };
  device: {
    model: string | null;
    nozzle_diameter: number | null;
    nozzle_type: string | null;
    wifi_signal_dbm: number | null;
    sd_card: boolean | null;
    light_on: boolean;
    camera_available: boolean;
    camera_resolution: string | null;
  };
  ams: {
    connected: boolean;
    current_tray: number | null;
    target_tray: number | null;
    trays: AmsTrayStatus[];
  };
  alerts: Array<{ code: string; message: string | null }>;
}

export interface RunRecord {
  id: number;
  name: string;
  file: string;
  started_at: string;
  ended_at: string | null;
  final_state: string | null;
  progress: number;
  layer: number;
  total_layers: number;
}
