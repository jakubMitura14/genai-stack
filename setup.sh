#!/bin/bash
# Create data directory if it doesn't exist
mkdir -p data

# Export user ID and group ID variables for docker-compose to use
# Using CURRENT_UID instead of UID since UID is a readonly shell variable
export CURRENT_UID=$(id -u)
export CURRENT_GID=$(id -g)

echo "Setup complete. Environment variables set:"
echo "CURRENT_UID=$CURRENT_UID"
echo "CURRENT_GID=$CURRENT_GID"
echo "Run the following command to launch:"
echo "CURRENT_UID=$CURRENT_UID CURRENT_GID=$CURRENT_GID docker compose --profile linux-gpu up"