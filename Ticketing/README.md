# Helix customer portal (on top of Zammad)

## Zammad setup (once)
1. Admin -> Settings -> System -> API: enable **API Token Access** and **API Password Access**.
2. Profile (your admin) -> Token Access: create a token with permissions `admin.user` (+ `ticket.agent`).
3. Make sure a group named `Users` exists (or set ZAMMAD_GROUP).
4. Create test customers (Admin -> Users, role Customer) with passwords.

## Run (on the VM or your laptop)
    pip install -r requirements.txt
    export ZAMMAD_URL=http://10.0.16.126:8080
    export ZAMMAD_TOKEN=<token>
    uvicorn main:app --host 0.0.0.0 --port 8000
Open http://<host>:8000 . The QR button encodes whatever address you opened.

## API (reusable by the chatbot)
GET /api/tickets  -> the signed-in customer's tickets with states.
