### Local Docker
cp .env.example .env          # fill your Claude key
docker compose up --build

### Deploy to Render / Railway
1. Push to GitHub
2. Connect repo → choose "Docker" build
3. Add ANTHROPIC_API_KEY as environment variable in dashboard
4. Done.