-- Migration: Add chart_path column to strategy_signals table
-- Date: 2026-01-18
-- Description: Adds chart_path column to store interactive chart file paths for strategy signals

-- Add chart_path column
ALTER TABLE strategy_signals
ADD COLUMN chart_path VARCHAR(500);

-- Create index for faster chart path lookups
CREATE INDEX idx_strategy_signals_chart_path
ON strategy_signals(chart_path)
WHERE chart_path IS NOT NULL;

-- Add comment
COMMENT ON COLUMN strategy_signals.chart_path IS 'Path to interactive Plotly chart HTML file';
