from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class MemberMessage:
    title: str
    body: str


# Keep the copy short enough to work well in Discord embeds. {member} is the
# already-formatted member mention/display name supplied by the caller.
WELCOME_MESSAGES: dict[str, tuple[MemberMessage, ...]] = {
    "EN": (
        MemberMessage("🚂 ALL ABOARD THE CRAZY TRAIN!", "{member}, welcome to **[OZY] Odyssey**! The gates are open, the bats are awake, and the Madhouse just got louder. 🤘🦇"),
        MemberMessage("🦇 THE BATS HAVE A NEW FRIEND", "{member} has entered **[OZY] Odyssey**. Find a seat, sharpen the troops, and try not to wake Ozzy. Too late. 🤘"),
        MemberMessage("🤘 WELCOME TO THE MADHOUSE", "The doors just slammed shut behind {member}. Welcome to **[OZY] Odyssey** - where strategy meets a healthy amount of chaos. 🦇"),
        MemberMessage("⚡ OZY JUST GOT LOUDER", "Turn it up. {member} has joined **[OZY] Odyssey**. The Crazy Train has one more passenger. 🚂🤘"),
        MemberMessage("💀 ANOTHER SOUL BOARDS THE TRAIN", "Welcome {member}. The bats approve, the train is moving, and **[OZY] Odyssey** is ready for more trouble. 🦇🚂"),
    ),
    "ES": (
        MemberMessage("🚂 ¡TODOS AL CRAZY TRAIN!", "{member}, bienvenido a **[OZY] Odyssey**. Las puertas están abiertas, los murciélagos despiertos y el Madhouse acaba de subir el volumen. 🤘🦇"),
        MemberMessage("🦇 LOS MURCIÉLAGOS TIENEN COMPAÑÍA", "{member} entró a **[OZY] Odyssey**. Busca un asiento, prepara las tropas y no despiertes a Ozzy. Demasiado tarde. 🤘"),
        MemberMessage("🤘 BIENVENIDO AL MADHOUSE", "Las puertas se cerraron detrás de {member}. Bienvenido a **[OZY] Odyssey**, donde la estrategia viene con una buena dosis de caos. 🦇"),
        MemberMessage("⚡ OZY ACABA DE SUBIR EL VOLUMEN", "Que suene fuerte. {member} se unió a **[OZY] Odyssey**. El Crazy Train lleva un pasajero más. 🚂🤘"),
        MemberMessage("💀 OTRA ALMA SUBE AL TREN", "Bienvenido {member}. Los murciélagos aprueban, el tren ya está en marcha y **[OZY] Odyssey** está listo para más lío. 🦇🚂"),
    ),
    "PT": (
        MemberMessage("🚂 TODOS A BORDO DO CRAZY TRAIN!", "{member}, bem-vindo à **[OZY] Odyssey**. Os portões estão abertos, os morcegos acordaram e o Madhouse ficou mais barulhento. 🤘🦇"),
        MemberMessage("🦇 OS MORCEGOS GANHARAM COMPANHIA", "{member} entrou na **[OZY] Odyssey**. Pegue um lugar, prepare as tropas e tente não acordar o Ozzy. Tarde demais. 🤘"),
        MemberMessage("🤘 BEM-VINDO AO MADHOUSE", "As portas se fecharam atrás de {member}. Bem-vindo à **[OZY] Odyssey**, onde estratégia e caos andam juntos. 🦇"),
        MemberMessage("⚡ OZY FICOU AINDA MAIS BARULHENTA", "Aumenta o volume. {member} entrou na **[OZY] Odyssey**. O Crazy Train ganhou mais um passageiro. 🚂🤘"),
        MemberMessage("💀 MAIS UMA ALMA EMBARCOU", "Bem-vindo {member}. Os morcegos aprovaram, o trem está andando e a **[OZY] Odyssey** está pronta para mais confusão. 🦇🚂"),
    ),
    "SV": (
        MemberMessage("🚂 ALLA OMBORD PÅ CRAZY TRAIN!", "{member}, välkommen till **[OZY] Odyssey**. Portarna är öppna, fladdermössen är vakna och Madhouse blev just lite högre. 🤘🦇"),
        MemberMessage("🦇 FLADDERMÖSSEN HAR FÅTT SÄLLSKAP", "{member} har klivit in i **[OZY] Odyssey**. Hitta en plats, gör trupperna redo och försök att inte väcka Ozzy. För sent. 🤘"),
        MemberMessage("🤘 VÄLKOMMEN TILL MADHOUSE", "Dörrarna slog igen bakom {member}. Välkommen till **[OZY] Odyssey**, där strategi möter en lagom dos kaos. 🦇"),
        MemberMessage("⚡ OZY BLEV JUST HÖGRE", "Skruva upp volymen. {member} har gått med i **[OZY] Odyssey**. Crazy Train har en passagerare till. 🚂🤘"),
        MemberMessage("💀 EN NY SJÄL KLIVER OMBORD", "Välkommen {member}. Fladdermössen godkänner, tåget rullar och **[OZY] Odyssey** är redo för mer kaos. 🦇🚂"),
    ),
    "DE": (
        MemberMessage("🚂 ALLE EINSTEIGEN IN DEN CRAZY TRAIN!", "{member}, willkommen bei **[OZY] Odyssey**. Die Tore sind offen, die Fledermäuse sind wach und das Madhouse ist gerade lauter geworden. 🤘🦇"),
        MemberMessage("🦇 DIE FLEDERMÄUSE HABEN GESELLSCHAFT", "{member} ist bei **[OZY] Odyssey** angekommen. Such dir einen Platz, mach die Truppen bereit und versuch Ozzy nicht zu wecken. Zu spät. 🤘"),
        MemberMessage("🤘 WILLKOMMEN IM MADHOUSE", "Die Türen sind hinter {member} zugefallen. Willkommen bei **[OZY] Odyssey**, wo Strategie auf eine gesunde Portion Chaos trifft. 🦇"),
        MemberMessage("⚡ OZY IST GERADE LAUTER GEWORDEN", "Dreh auf. {member} ist **[OZY] Odyssey** beigetreten. Der Crazy Train hat einen Fahrgast mehr. 🚂🤘"),
        MemberMessage("💀 EINE WEITERE SEELE STEIGT EIN", "Willkommen {member}. Die Fledermäuse sind einverstanden, der Zug rollt und **[OZY] Odyssey** ist bereit für mehr Chaos. 🦇🚂"),
    ),
    "CEB": (
        MemberMessage("🚂 SAKAY NA SA CRAZY TRAIN!", "{member}, maayong pag-abot sa **[OZY] Odyssey**. Abli na ang mga ganghaan, nagmata na ang mga kabog, ug mas nisaba na ang Madhouse. 🤘🦇"),
        MemberMessage("🦇 NAAY BAG-ONG KAUBAN ANG MGA KABOG", "Nisulod na si {member} sa **[OZY] Odyssey**. Pangitag lingkoranan, andama ang troops, ug ayaw unta pukawa si Ozzy. Ulahi na. 🤘"),
        MemberMessage("🤘 MAAYONG PAG-ABOT SA MADHOUSE", "Nisirado na ang mga pultahan sa luyo ni {member}. Maayong pag-abot sa **[OZY] Odyssey**, diin ang strategy adunay sakto nga kagubot. 🦇"),
        MemberMessage("⚡ MAS NISABA PA ANG OZY", "Pasaka ang volume. Miapil na si {member} sa **[OZY] Odyssey**. Nadugangan na pud ang pasahero sa Crazy Train. 🚂🤘"),
        MemberMessage("💀 USA NA PUD KA KALAG MISAKAY", "Maayong pag-abot {member}. Uyon ang mga kabog, nagdagan na ang tren, ug andam ang **[OZY] Odyssey** sa dugang kagubot. 🦇🚂"),
    ),
    "FR": (
        MemberMessage("🚂 TOUT LE MONDE À BORD DU CRAZY TRAIN !", "{member}, bienvenue chez **[OZY] Odyssey**. Les portes sont ouvertes, les chauves-souris sont réveillées et le Madhouse vient de monter le volume. 🤘🦇"),
        MemberMessage("🦇 LES CHAUVES-SOURIS ONT DE LA COMPAGNIE", "{member} vient d'entrer chez **[OZY] Odyssey**. Trouve une place, prépare les troupes et essaie de ne pas réveiller Ozzy. Trop tard. 🤘"),
        MemberMessage("🤘 BIENVENUE AU MADHOUSE", "Les portes viennent de se refermer derrière {member}. Bienvenue chez **[OZY] Odyssey**, là où la stratégie rencontre une bonne dose de chaos. 🦇"),
        MemberMessage("⚡ OZY VIENT DE MONTER LE VOLUME", "Monte le son. {member} a rejoint **[OZY] Odyssey**. Le Crazy Train compte un passager de plus. 🚂🤘"),
        MemberMessage("💀 UNE ÂME DE PLUS MONTE À BORD", "Bienvenue {member}. Les chauves-souris approuvent, le train roule et **[OZY] Odyssey** est prêt pour encore plus de chaos. 🦇🚂"),
    ),
    "RU": (
        MemberMessage("🚂 ВСЕ НА БОРТ CRAZY TRAIN!", "{member}, добро пожаловать в **[OZY] Odyssey**. Ворота открыты, летучие мыши проснулись, а Madhouse стал ещё громче. 🤘🦇"),
        MemberMessage("🦇 У ЛЕТУЧИХ МЫШЕЙ НОВАЯ КОМПАНИЯ", "{member} вошёл в **[OZY] Odyssey**. Выбирай место, готовь войска и постарайся не разбудить Оззи. Уже поздно. 🤘"),
        MemberMessage("🤘 ДОБРО ПОЖАЛОВАТЬ В MADHOUSE", "Двери захлопнулись за {member}. Добро пожаловать в **[OZY] Odyssey**, где стратегия встречается со здоровой дозой хаоса. 🦇"),
        MemberMessage("⚡ OZY СТАЛ ЕЩЁ ГРОМЧЕ", "Добавь громкости. {member} присоединился к **[OZY] Odyssey**. В Crazy Train стало на одного пассажира больше. 🚂🤘"),
        MemberMessage("💀 ЕЩЁ ОДНА ДУША НА БОРТУ", "Добро пожаловать, {member}. Летучие мыши одобряют, поезд уже идёт, а **[OZY] Odyssey** готов к новому хаосу. 🦇🚂"),
    ),
    "AR": (
        MemberMessage("🚂 الجميع إلى CRAZY TRAIN!", "{member}، أهلاً بك في **[OZY] Odyssey**. البوابات مفتوحة، والخفافيش مستيقظة، والـ Madhouse أصبح أعلى صوتاً. 🤘🦇"),
        MemberMessage("🦇 الخفافيش لديها رفيق جديد", "دخل {member} إلى **[OZY] Odyssey**. اختر مقعدك، جهّز قواتك، وحاول ألا توقظ Ozzy. فات الأوان. 🤘"),
        MemberMessage("🤘 أهلاً بك في MADHOUSE", "أُغلقت الأبواب خلف {member}. أهلاً بك في **[OZY] Odyssey**، حيث تلتقي الاستراتيجية بجرعة صحية من الفوضى. 🦇"),
        MemberMessage("⚡ OZY أصبح أعلى صوتاً", "ارفع الصوت. انضم {member} إلى **[OZY] Odyssey**. أصبح لدى Crazy Train راكب جديد. 🚂🤘"),
        MemberMessage("💀 روح جديدة صعدت إلى القطار", "أهلاً بك {member}. الخفافيش موافقة، والقطار انطلق، و**[OZY] Odyssey** جاهز لمزيد من الفوضى. 🦇🚂"),
    ),
    "NO": (
        MemberMessage("🚂 ALLE OMBORD PÅ CRAZY TRAIN!", "{member}, velkommen til **[OZY] Odyssey**. Portene er åpne, flaggermusene er våkne og Madhouse ble nettopp litt høyere. 🤘🦇"),
        MemberMessage("🦇 FLAGGERMUSENE HAR FÅTT SELSKAP", "{member} har kommet inn i **[OZY] Odyssey**. Finn en plass, gjør troppene klare og prøv å ikke vekke Ozzy. For sent. 🤘"),
        MemberMessage("🤘 VELKOMMEN TIL MADHOUSE", "Dørene smalt igjen bak {member}. Velkommen til **[OZY] Odyssey**, der strategi møter en sunn dose kaos. 🦇"),
        MemberMessage("⚡ OZY BLE NETTOPP LITT HØYERE", "Skru opp lyden. {member} har blitt med i **[OZY] Odyssey**. Crazy Train har fått én passasjer til. 🚂🤘"),
        MemberMessage("💀 EN NY SJEL GÅR OMBORD", "Velkommen {member}. Flaggermusene godkjenner, toget ruller og **[OZY] Odyssey** er klart for mer kaos. 🦇🚂"),
    ),
}


GOODBYE_MESSAGES: dict[str, tuple[MemberMessage, ...]] = {
    "EN": (
        MemberMessage("🦇 ANOTHER BAT LEAVES THE BELFRY", "**{member}** has left **[OZY] Odyssey**. The bats raise a wing in salute. Safe travels beyond the gates. 🤘"),
        MemberMessage("🚂 ONE PASSENGER STEPS OFF", "**{member}** has left the Crazy Train. The music keeps playing and **[OZY] Odyssey** rolls on. 🤘"),
        MemberMessage("🌙 FLY SAFE", "**{member}** has left the Madhouse. May the road ahead be loud and the bats friendly. 🦇"),
    ),
    "ES": (
        MemberMessage("🦇 OTRO MURCIÉLAGO DEJA EL CAMPANARIO", "**{member}** dejó **[OZY] Odyssey**. Los murciélagos levantan un ala en señal de despedida. Buen viaje más allá de las puertas. 🤘"),
        MemberMessage("🚂 UN PASAJERO BAJA DEL TREN", "**{member}** dejó el Crazy Train. La música sigue sonando y **[OZY] Odyssey** sigue adelante. 🤘"),
        MemberMessage("🌙 BUEN VUELO", "**{member}** dejó el Madhouse. Que el camino sea ruidoso y los murciélagos amistosos. 🦇"),
    ),
    "PT": (
        MemberMessage("🦇 MAIS UM MORCEGO DEIXA O CAMPANÁRIO", "**{member}** deixou a **[OZY] Odyssey**. Os morcegos levantam uma asa em despedida. Boa viagem além dos portões. 🤘"),
        MemberMessage("🚂 UM PASSAGEIRO DESCE DO TREM", "**{member}** deixou o Crazy Train. A música continua e a **[OZY] Odyssey** segue em frente. 🤘"),
        MemberMessage("🌙 BOM VOO", "**{member}** deixou o Madhouse. Que a estrada seja barulhenta e os morcegos amigáveis. 🦇"),
    ),
    "SV": (
        MemberMessage("🦇 EN FLADDERMUS LÄMNAR KLOCKTORNET", "**{member}** har lämnat **[OZY] Odyssey**. Fladdermössen höjer en vinge till avsked. Lycka till bortom portarna. 🤘"),
        MemberMessage("🚂 EN PASSAGERARE KLIVER AV", "**{member}** har lämnat Crazy Train. Musiken fortsätter och **[OZY] Odyssey** rullar vidare. 🤘"),
        MemberMessage("🌙 FLYG SÄKERT", "**{member}** har lämnat Madhouse. Må vägen framåt vara högljudd och fladdermössen vänliga. 🦇"),
    ),
    "DE": (
        MemberMessage("🦇 EINE FLEDERMAUS VERLÄSST DEN TURM", "**{member}** hat **[OZY] Odyssey** verlassen. Die Fledermäuse heben zum Abschied einen Flügel. Gute Reise jenseits der Tore. 🤘"),
        MemberMessage("🚂 EIN FAHRGAST STEIGT AUS", "**{member}** hat den Crazy Train verlassen. Die Musik läuft weiter und **[OZY] Odyssey** rollt weiter. 🤘"),
        MemberMessage("🌙 GUTEN FLUG", "**{member}** hat das Madhouse verlassen. Möge der Weg laut und die Fledermäuse freundlich sein. 🦇"),
    ),
    "CEB": (
        MemberMessage("🦇 USA KA KABOG MIBIYA SA TORE", "Mibiya na si **{member}** sa **[OZY] Odyssey**. Nagpataas og pako ang mga kabog isip panamilit. Amping sa biyahe. 🤘"),
        MemberMessage("🚂 USA KA PASAHERO MIKANAOG", "Mibiya na si **{member}** sa Crazy Train. Padayon ang musika ug padayon usab ang **[OZY] Odyssey**. 🤘"),
        MemberMessage("🌙 AMPING SA PAGLUPAD", "Mibiya na si **{member}** sa Madhouse. Hinaot saba ang dalan ug buotan ang mga kabog. 🦇"),
    ),
    "FR": (
        MemberMessage("🦇 UNE CHAUVE-SOURIS QUITTE LE CLOCHER", "**{member}** a quitté **[OZY] Odyssey**. Les chauves-souris lèvent une aile en guise de salut. Bonne route au-delà des portes. 🤘"),
        MemberMessage("🚂 UN PASSAGER DESCEND DU TRAIN", "**{member}** a quitté le Crazy Train. La musique continue et **[OZY] Odyssey** poursuit sa route. 🤘"),
        MemberMessage("🌙 BON VOL", "**{member}** a quitté le Madhouse. Que la route soit bruyante et les chauves-souris amicales. 🦇"),
    ),
    "RU": (
        MemberMessage("🦇 ЕЩЁ ОДНА ЛЕТУЧАЯ МЫШЬ ПОКИДАЕТ БАШНЮ", "**{member}** покинул **[OZY] Odyssey**. Летучие мыши поднимают крыло на прощание. Счастливого пути за воротами. 🤘"),
        MemberMessage("🚂 ОДИН ПАССАЖИР ВЫХОДИТ", "**{member}** покинул Crazy Train. Музыка продолжает играть, а **[OZY] Odyssey** движется дальше. 🤘"),
        MemberMessage("🌙 СЧАСТЛИВОГО ПОЛЁТА", "**{member}** покинул Madhouse. Пусть дорога будет громкой, а летучие мыши дружелюбными. 🦇"),
    ),
    "AR": (
        MemberMessage("🦇 خفاش آخر يغادر البرج", "غادر **{member}** **[OZY] Odyssey**. ترفع الخفافيش جناحاً للتحية. رحلة آمنة خلف البوابات. 🤘"),
        MemberMessage("🚂 راكب يغادر القطار", "غادر **{member}** Crazy Train. الموسيقى مستمرة و**[OZY] Odyssey** يواصل الرحلة. 🤘"),
        MemberMessage("🌙 طيران آمن", "غادر **{member}** الـ Madhouse. نتمنى أن يكون الطريق صاخباً والخفافيش ودودة. 🦇"),
    ),
    "NO": (
        MemberMessage("🦇 EN FLAGGERMUS FORLATER TÅRNET", "**{member}** har forlatt **[OZY] Odyssey**. Flaggermusene løfter en vinge til farvel. God tur videre utenfor portene. 🤘"),
        MemberMessage("🚂 EN PASSASJER GÅR AV", "**{member}** har forlatt Crazy Train. Musikken fortsetter og **[OZY] Odyssey** ruller videre. 🤘"),
        MemberMessage("🌙 FLY TRYGT", "**{member}** har forlatt Madhouse. Må veien være høylytt og flaggermusene vennlige. 🦇"),
    ),
}


def normalize_language(language: str | None) -> str:
    code = str(language or "").strip().upper()
    return code if code in WELCOME_MESSAGES else "EN"


def choose_welcome(language: str | None) -> MemberMessage:
    return random.choice(WELCOME_MESSAGES[normalize_language(language)])


def choose_goodbye(language: str | None) -> MemberMessage:
    return random.choice(GOODBYE_MESSAGES[normalize_language(language)])
