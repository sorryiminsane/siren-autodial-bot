-- Add agent_telegram_id column to autodial_calls table
ALTER TABLE autodial_calls 
ADD COLUMN IF NOT EXISTS agent_telegram_id BIGINT;

-- Create index for better query performance
CREATE INDEX IF NOT EXISTS idx_autodial_calls_agent_telegram_id 
ON autodial_calls(agent_telegram_id);

-- Update existing records to set agent_telegram_id from campaign relationship
UPDATE autodial_calls ac
SET agent_telegram_id = c.agent_telegram_id
FROM autodial_campaigns c
WHERE ac.campaign_id = c.id
AND ac.agent_telegram_id IS NULL; 