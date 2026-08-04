## Testing from scratch
1. stop connections via KERIGuardIDM vpn user plugin, if they exist
2. Run `ifconfig | grep -A3 utun` to check that connections were properly stopped
3. remove keriguard-helper resources
    - pgrep -fl KERIGuardHelper
    - `kill <pid>` for any that show up
    - Remove keriguard helper app from settings -> general -> login items and extensions -> network extensions (Maybe try doing this first?)
    - sudo sfltool resetbtm
    - rm -rf /Users/arilieb/keriguard/*
4. remove KERIGuard app from applications
    - rm -rf ~/Library/Containers/com.healthkeri.locksmith
5. Remove guardian and sentinel daemons:
    - Check status
        - launchctl print gui/$(id -u)/com.healthkeri.keriguard.guardian
        - launchctl print gui/$(id -u)/com.healthkeri.keriguard.sentinel
    - launchctl bootout gui/$(id -u)/com.healthkeri.keriguard.guardian
    - launchctl bootout gui/$(id -u)/com.healthkeri.keriguard.sentinel
    - rm -rf ~/Library/Application\ Support/KERIGuard
    - rm -f ~/Library/LaunchAgents/com.healthkeri.keriguard.{guardian,sentinel}.plist
6. Clear keri databases:
    - rm -rf /usr/local/var/keri/*
    - rm -rf ~/.keriguard/*
7. Run `docker compose down -v` from nightingale if the docker container is up
8. restart the computer
9. Download fully built app .dmg off of DO
10. Install into applications via dmg
11. Set up Docker environment from /Users/arilieb/healthkeri/keriopnet/nightingale
    - To start local SaaS platform from scratch
        - docker compose build
        - docker compose up -d --force-recreate
    - To restart
        - docker compose down -v
        - docker compose up -d --force-recreate
    - To fully rebuild and restart
        - docker compose build --no-cache
        - docker compose up -d --force-recreate
    - To see logs or access mongodb
        - docker compose logs -f witness-demo (or saas-platform or other)
        - docker exec -it healthkeri-mongodb mongosh
12. Start logged KERIGuardIDM app session via iTerm2 two terminal split
    - Terminal 1:
        - export SSL_CERT_FILE=$(python -c "import certifi; print(certifi.where())")
        - export REQUESTS_CA_BUNDLE=$(python -c "import certifi; print(certifi.where())")
        - `export ARCHIMEDES_ENVIRONMENT=development`
        - `export LOCKSMITH_ENVIRONMENT=development`
        - `/Applications/KERIGuard.app/Contents/MacOS/KERIGuard > /tmp/KERIGuard.log 2>&1`
    - Terminal 2:
        - `tail -f /tmp/KERIGuard.log`
- From the Keriguard app:
    - First we create an admin vault along with an admin identity and a healthKERI account identity. Create a healthKERI account with the identity and upload the admin identity, then provision a witness for it. Navigate to the keriguard admin plugin and select admin identity as the issuer and set the export directory, then complete the setup. Provision a machine (/Users/arilieb/healthkeri/keriguard-plugin/plugins/admin/src/keriguard_admin/machines/add.py) and save the config file. Exit the vault
        - Use /Users/arilieb/keriguard-admin-export/ for admin export dir in setup
    - Create a peer1 vault along with a healthKERI account identity. Create a healthKERI account with the identity. proceed through /Users/arilieb/healthkeri/keriguard-plugin/plugins/user/src/keriguard_user/setup/page.py, using the config file saved through the admin plugin, set the wireguard config export directory, then attempt to initialize. The initialization appears successful according to the UI but the following occurs in the logs.
        - Use /Users/arilieb/keriguard-admin-export/sentinel-config.yaml for user config file, and /Users/arilieb/keriguard for wireguard config dir.
    - Return to admin plugin in admin vault and access /Users/arilieb/healthkeri/keriguard-plugin/plugins/admin/src/keriguard_admin/machines/view.py for the machine that was created. Click the "Issue IP Address" button, which appears to issue successfully.
    - Return to the user plugin in the peer1 vault and the machine credential is autopopulated and active in /Users/arilieb/healthkeri/keriguard-plugin/plugins/user/src/keriguard_user/machines/list.py.
- Also, the 2 items (kg-guardian and sentinel) still appear under "Ari Lieb Francois Argoud" in Settings -> General -> Login Items & Extensions -> Allow In Background, which ws supposed to have been addressed.

### Diagnostic Commands

Start with low value for -n flag and increase if indicated
- tail -n 3 ~/Library/Application\ Support/KERIGuard/logs/guardian.stderr.log
- tail -n 3 ~/Library/Application\ Support/KERIGuard/logs/sentinel.stderr.log
- head -n 3 ~/Library/Application\ Support/KERIGuard/logs/guardian.stderr.log
- head -n 3 ~/Library/Application\ Support/KERIGuard/logs/sentinel.stderr.log 