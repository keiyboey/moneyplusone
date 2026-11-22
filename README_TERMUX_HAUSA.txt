MONEY PLUS ONE — PHASE 2 (TERMUX)

ABUBUWAN DA AKA HADA:
- Homepage dinka (an bar HTML/CSS dinsa kamar yadda ka bayar).
- Register da Login.
- SQLite database da password hashing.
- User dashboard.
- Admin dashboard daban da na user.
- Logout.
- Basic CSRF token a POST forms.

SHIGARWA A TERMUX:
1. Ka ajiye tsohon project a gefe kafin ka maye gurbinsa.
2. Ka bude Termux ka shiga sabon folder:
   cd ~/storage/downloads/MONEY_PLUS_ONE_PHASE_2
   (Idan ka kwafe folder zuwa wani wuri, yi cd zuwa inda yake.)
3. Saita Python da pip idan ba su nan:
   pkg update && pkg upgrade
   pkg install python
4. Sanya Flask:
   pip install -r requirements.txt
5. Kaddamar da website:
   python app.py
6. A browser na wayarka bude:
   http://127.0.0.1:5000

KIRKIRAR ADMIN:
A bude sabon Termux tab ko a dakatar da server da CTRL+C, sannan:
   flask --app app create-admin
Za a tambaye ka sunan admin, email, da password. Password din ba zai bayyana yayin rubutawa ba.
Sannan sake kunna server:
   python app.py
Shiga /login da admin email/password; zai kai ka /admin.

MUHIMMI:
- Register yana kirkirar USER ne kawai. Ba zai iya kirkirar admin ba.
- Admin account sai ta create-admin command.
- Kada ka saka ainihin password ko secret key a fili.
- Kafin deployment na internet, canza MPO_SECRET_KEY zuwa dogon sirri mai karfi, kashe debug mode, kunna HTTPS da secure cookies, sannan a yi security review.
- Wannan starter bai karbi kudi ko sarrafa investment ba. Kada a karbi kudin jama'a har sai an tabbatar da lasisi, tsarin doka, payment-provider approval, bank compliance da tsaron bayanai.
