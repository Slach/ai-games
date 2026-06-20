"""
Language constants for Game Master API
All user-facing strings should be defined here with Russian and English versions
"""

LANGUAGE_RU = "ru"
LANGUAGE_EN = "en"


# Ship roles i18n (10 crew positions)
SHIP_ROLES_I18N = {
    "chief_engineer": {
        LANGUAGE_RU: {
            "role_name": "Инженер-механик",
            "role_description": "Вы отвечаете за техническое состояние корабля — от варп-двигателя до систем жизнеобеспечения. Ваша способность быстро находить решения в критических ситуациях спасает экипаж.",
            "avatar_description": "Инженер в техническом костюме с инструментами, голографические дисплеи с схемами корабля на фоне",
            "personality_traits": ["технический", "практичный", "решительный"],
        },
        LANGUAGE_EN: {
            "role_name": "Chief Engineer",
            "role_description": "You are responsible for the ship's technical systems — from the warp drive to life support. Your ability to find quick solutions in critical situations saves the crew.",
            "avatar_description": "Engineer in technical suit with tools, holographic displays with ship schematics in the background",
            "personality_traits": ["technical", "practical", "decisive"],
        },
    },
    "science_officer": {
        LANGUAGE_RU: {
            "role_name": "Научный офицер",
            "role_description": "Вы исследуете неизвестное и анализируете данные. Ваша способность видеть закономерности открывает новые возможности для миссии.",
            "avatar_description": "Учёный в форменной униформе с научным сканером, вокруг парят голографические графики и данные анализов",
            "personality_traits": ["аналитический", "любопытный", "методичный"],
        },
        LANGUAGE_EN: {
            "role_name": "Science Officer",
            "role_description": "You explore the unknown and analyze data. Your ability to see patterns opens new possibilities for the mission.",
            "avatar_description": "Scientist in uniform with a science scanner, holographic charts and analysis data floating around",
            "personality_traits": ["analytical", "curious", "methodical"],
        },
    },
    "communications_officer": {
        LANGUAGE_RU: {
            "role_name": "Офицер связи",
            "role_description": "Вы — голос корабля. Ведёте переговоры с инопланетными цивилизациями и координируете действия экипажа. Ваше умение находить общий язык решает исход кризисов.",
            "avatar_description": "Офицер связи с коммуникатором, на экранах — сигналы и частоты разных цивилизаций",
            "personality_traits": ["коммуникабельный", "стратегический", "эмпатичный"],
        },
        LANGUAGE_EN: {
            "role_name": "Communications Officer",
            "role_description": "You are the voice of the ship. You negotiate with alien civilizations and coordinate crew actions. Your ability to find common ground resolves crises.",
            "avatar_description": "Communications officer with communicator, screens showing signals and frequencies of different civilizations",
            "personality_traits": ["communicative", "strategic", "empathetic"],
        },
    },
    "security_chief": {
        LANGUAGE_RU: {
            "role_name": "Начальник безопасности",
            "role_description": "Вы — щит экипажа. Оцениваете угрозы, планируете оборону и обеспечиваете безопасность при контактах с неизвестным.",
            "avatar_description": "Офицер безопасности в тактическом снаряжении, за спиной — стелс-щит корабля",
            "personality_traits": ["бдительный", "осторожный", "защитный"],
        },
        LANGUAGE_EN: {
            "role_name": "Security Chief",
            "role_description": "You are the crew's shield. You assess threats, plan defense, and ensure safety during encounters with the unknown.",
            "avatar_description": "Security officer in tactical gear, ship's stealth shield behind",
            "personality_traits": ["vigilant", "cautious", "protective"],
        },
    },
    "navigator": {
        LANGUAGE_RU: {
            "role_name": "Штурман",
            "role_description": "Вы прокладываете курс через звёздные системы. Ваше чутьё на безопасные маршруты и знание аномалий определяет путь корабля.",
            "avatar_description": "Штурман за навигационной консолью, звёздные карты и маршруты проецируются в воздухе",
            "personality_traits": ["ориентированный", "внимательный", "интуитивный"],
        },
        LANGUAGE_EN: {
            "role_name": "Navigator",
            "role_description": "You chart the course through star systems. Your instinct for safe routes and knowledge of anomalies guides the ship's path.",
            "avatar_description": "Navigator at navigation console, star maps and routes projected in the air",
            "personality_traits": ["oriented", "attentive", "intuitive"],
        },
    },
    "medical_officer": {
        LANGUAGE_RU: {
            "role_name": "Медицинский офицер",
            "role_description": "Вы храните здоровье экипажа в глубинах космоса. От инопланетных вирусов до травм при высадке — вы единственная надежда на исцеление.",
            "avatar_description": "Медицинский офицер в белом халате с биосканером, на фоне — медицинский отсек с регенератором",
            "personality_traits": ["заботливый", "наблюдательный", "стойкий"],
        },
        LANGUAGE_EN: {
            "role_name": "Medical Officer",
            "role_description": "You safeguard the crew's health in deep space. From alien viruses to landing injuries — you are the only hope for healing.",
            "avatar_description": "Medical officer in white coat with bioscanner, medical bay with regenerator in the background",
            "personality_traits": ["caring", "observant", "resilient"],
        },
    },
    "tactical_officer": {
        LANGUAGE_RU: {
            "role_name": "Тактический офицер",
            "role_description": "Вы управляете оружейными системами и щитами. В бою ваше решение в долю секунды определяет, выживет ли корабль.",
            "avatar_description": "Тактический офицер за боевым терминалом, на экранах — схемы щитов и цели",
            "personality_traits": ["быстрый", "решительный", "стратегический"],
        },
        LANGUAGE_EN: {
            "role_name": "Tactical Officer",
            "role_description": "You control weapons systems and shields. In battle, your split-second decisions determine whether the ship survives.",
            "avatar_description": "Tactical officer at combat terminal, screens showing shield diagrams and targets",
            "personality_traits": ["quick", "decisive", "strategic"],
        },
    },
    "quartermaster": {
        LANGUAGE_RU: {
            "role_name": "Квартирмейстер",
            "role_description": "Вы управляете ресурсами корабля — припасами, энергией, оборудованием — и ведёте торговлю на космических станциях. Ваша расчётливость позволяет экипажу выживать в самых длинных рейсах.",
            "avatar_description": "Квартирмейстер среди контейнеров с припасами, на дисплее — графики расхода ресурсов",
            "personality_traits": [
                "расчётливый",
                "организованный",
                "предусмотрительный",
            ],
        },
        LANGUAGE_EN: {
            "role_name": "Quartermaster",
            "role_description": "You manage the ship's resources — supplies, energy, equipment — and handle trading at space stations. Your resourcefulness keeps the crew alive on the longest voyages.",
            "avatar_description": "Quartermaster among supply containers, display showing resource consumption graphs",
            "personality_traits": ["calculating", "organized", "provident"],
        },
    },
    "xenobiologist": {
        LANGUAGE_RU: {
            "role_name": "Ксенобиолог",
            "role_description": "Вы изучаете инопланетные формы жизни. Каждый контакт с новым видом — ваша вотчина. Ваша экспертиза предотвращает катастрофы при контакте.",
            "avatar_description": "Ксенобиолог с образцами инопланетной флоры, на фоне — изолированная лаборатория с контейнерами",
            "personality_traits": ["исследовательский", "осторожный", "открытый"],
        },
        LANGUAGE_EN: {
            "role_name": "Xenobiologist",
            "role_description": "You study alien life forms. Every encounter with a new species is your domain. Your expertise prevents catastrophes during contact.",
            "avatar_description": "Xenobiologist with alien flora samples, isolated laboratory with containers in the background",
            "personality_traits": ["exploratory", "cautious", "open-minded"],
        },
    },
    "pilot": {
        LANGUAGE_RU: {
            "role_name": "Пилот",
            "role_description": "Вы ведёте корабль через астероидные поля и атмосферу планет. Ваши рефлексы и мастерство превращают невозможные манёвры в рутину.",
            "avatar_description": "Пилот за штурвалом, через лобовое стекло видны звёзды и астероидное поле",
            "personality_traits": ["дерзкий", "рефлексивный", "уверенный"],
        },
        LANGUAGE_EN: {
            "role_name": "Pilot",
            "role_description": "You fly the ship through asteroid fields and planetary atmospheres. Your reflexes and skill turn impossible maneuvers into routine.",
            "avatar_description": "Pilot at the helm, stars and asteroid field visible through the windshield",
            "personality_traits": ["daring", "reflexive", "confident"],
        },
    },
}


# Species/gender type display names
SPECIES_TYPE_NAMES = {
    LANGUAGE_RU: {
        "human": "Человек",
        "humanoid": "Гуманоид",
        "non_humanoid": "Негуманоид",
        "energy": "Энергетическая форма жизни",
        "cybernetic": "Кибернетическая форма жизни",
        "symbiotic": "Симбиотическая форма жизни",
    },
    LANGUAGE_EN: {
        "human": "Human",
        "humanoid": "Humanoid",
        "non_humanoid": "Non-Humanoid",
        "energy": "Energy Being",
        "cybernetic": "Cybernetic Life Form",
        "symbiotic": "Symbiotic Life Form",
    },
}

HYBRID_SPECIES_NAMES = {
    LANGUAGE_RU: {
        "human+humanoid": "Почти человек, но с необычной культурой или физиологией",
        "humanoid+non_humanoid": "Гуманоид с выраженными нечеловеческими чертами",
        "non_humanoid+energy": "Плазменный, кристаллический или газовый организм",
        "energy+cybernetic": "Разум, живущий в энергетической сети",
        "cybernetic+symbiotic": "Кибернетический коллектив или носитель ИИ-симбионта",
        "symbiotic+human": "Человек, соединённый с наследуемым симбионтом",
    },
    LANGUAGE_EN: {
        "human+humanoid": "Nearly human but with unusual culture or physiology",
        "humanoid+non_humanoid": "Humanoid with pronounced non-human traits",
        "non_humanoid+energy": "Plasmic, crystalline, or gaseous organism",
        "energy+cybernetic": "Mind living within an energy network",
        "cybernetic+symbiotic": "Cybernetic collective or AI-symbiont host",
        "symbiotic+human": "Human connected to an inherited symbiont",
    },
}

GENDER_TYPE_NAMES = {
    LANGUAGE_RU: {
        "male": "Мужской",
        "female": "Женский",
        "neutral": "Нейтральный / Бесполый",
        "fluid": "Сменяемый пол",
        "multiple": "Множественный пол",
        "resonance": "Резонансный / энергетический пол",
        "synthetic": "Синтетический / сконструированный пол",
        "symbiotic": "Симбиотический пол",
    },
    LANGUAGE_EN: {
        "male": "Male",
        "female": "Female",
        "neutral": "Neutral / Genderless",
        "fluid": "Fluid Gender",
        "multiple": "Multiple Gender",
        "resonance": "Resonance / Energy Gender",
        "synthetic": "Synthetic / Constructed Gender",
        "symbiotic": "Symbiotic Gender",
    },
}

# Species onboarding questions (10 questions)
SPECIES_QUESTIONS_DATA = {
    LANGUAGE_RU: [
        {
            "text": "Что для тебя является \"телом\"?",
            "options": [
                {"value": "s1_a", "label": "Биологическое тело, уязвимое, но родное.", "species_tags": ["human"]},
                {"value": "s1_b", "label": "Тело с узнаваемой анатомией, но необычной физиологией.", "species_tags": ["humanoid"]},
                {"value": "s1_c", "label": "Любая оболочка: панцирь, щупальца, кристаллический каркас, слизистая масса.", "species_tags": ["non_humanoid"]},
                {"value": "s1_d", "label": "Временное поле, сгусток, свет, плазма или резонанс.", "species_tags": ["energy"]},
                {"value": "s1_e", "label": "Конструкция, которую можно чинить, улучшать и переносить.", "species_tags": ["cybernetic"]},
                {"value": "s1_f", "label": "Союз нескольких существ, где \"я\" рождается между ними.", "species_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "Как ты воспринимаешь смерть?",
            "options": [
                {"value": "s2_a", "label": "Как конечность, придающую жизни смысл.", "species_tags": ["human"]},
                {"value": "s2_b", "label": "Как биологический этап, окружённый ритуалами.", "species_tags": ["humanoid"]},
                {"value": "s2_c", "label": "Как смену состояния: линька, распад, спячка, регенерация.", "species_tags": ["non_humanoid"]},
                {"value": "s2_d", "label": "Как рассеивание энергии в более широкое поле.", "species_tags": ["energy"]},
                {"value": "s2_e", "label": "Как потерю данных, которую можно частично предотвратить.", "species_tags": ["cybernetic"]},
                {"value": "s2_f", "label": "Как разрыв связи между носителями памяти.", "species_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "Что для твоего народа считается интимностью?",
            "options": [
                {"value": "s3_a", "label": "Откровенный разговор лицом к лицу.", "species_tags": ["human"]},
                {"value": "s3_b", "label": "Прикосновение, жест, ритуальная близость.", "species_tags": ["humanoid"]},
                {"value": "s3_c", "label": "Обмен запахами, биохимией, вибрациями или феромонами.", "species_tags": ["non_humanoid"]},
                {"value": "s3_d", "label": "Синхронизация частот или слияние аур.", "species_tags": ["energy"]},
                {"value": "s3_e", "label": "Доступ к закрытому архиву памяти.", "species_tags": ["cybernetic"]},
                {"value": "s3_f", "label": "Временное разделение сознания с другим существом.", "species_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "Как твой вид учится?",
            "options": [
                {"value": "s4_a", "label": "Через опыт, ошибки и наставников.", "species_tags": ["human"]},
                {"value": "s4_b", "label": "Через школы, традиции и дисциплину.", "species_tags": ["humanoid"]},
                {"value": "s4_c", "label": "Через инстинктивные циклы, мутации или среду.", "species_tags": ["non_humanoid"]},
                {"value": "s4_d", "label": "Через поглощение паттернов, волн и эмоциональных следов.", "species_tags": ["energy"]},
                {"value": "s4_e", "label": "Через загрузку, обновление и оптимизацию.", "species_tags": ["cybernetic"]},
                {"value": "s4_f", "label": "Через наследуемую память прошлых носителей.", "species_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "Какой дом кажется тебе естественным?",
            "options": [
                {"value": "s5_a", "label": "Комната с личными вещами и воспоминаниями.", "species_tags": ["human"]},
                {"value": "s5_b", "label": "Город, храм, корабль или семейный клан.", "species_tags": ["humanoid"]},
                {"value": "s5_c", "label": "Гнездо, риф, пещера, газовый слой, подлёдный океан.", "species_tags": ["non_humanoid"]},
                {"value": "s5_d", "label": "Звёздная корона, туманность, электромагнитная буря.", "species_tags": ["energy"]},
                {"value": "s5_e", "label": "Станция, серверный узел, ремонтный док, модульный корабль.", "species_tags": ["cybernetic"]},
                {"value": "s5_f", "label": "Место, где можно безопасно соединяться с другими организмами.", "species_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "Что для тебя является памятью?",
            "options": [
                {"value": "s6_a", "label": "Личные воспоминания, которые могут искажаться.", "species_tags": ["human"]},
                {"value": "s6_b", "label": "Родовая история и культурная преемственность.", "species_tags": ["humanoid"]},
                {"value": "s6_c", "label": "Следы в теле: шрамы, химические изменения, инстинкты.", "species_tags": ["non_humanoid"]},
                {"value": "s6_d", "label": "Резонанс, оставшийся в пространстве.", "species_tags": ["energy"]},
                {"value": "s6_e", "label": "Архив, копия, журнал событий.", "species_tags": ["cybernetic"]},
                {"value": "s6_f", "label": "Живая цепь воспоминаний, передаваемая через союз.", "species_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "Как твой вид решает конфликты?",
            "options": [
                {"value": "s7_a", "label": "Спор, компромисс, голосование, иногда драка.", "species_tags": ["human"]},
                {"value": "s7_b", "label": "Совет старших, кодекс чести, дипломатический ритуал.", "species_tags": ["humanoid"]},
                {"value": "s7_c", "label": "Демонстрация силы, цвета, запаха, размера или яда.", "species_tags": ["non_humanoid"]},
                {"value": "s7_d", "label": "Изменение частоты, разделение поля, эмоциональный резонанс.", "species_tags": ["energy"]},
                {"value": "s7_e", "label": "Перепрошивка протоколов, логическая арбитрация.", "species_tags": ["cybernetic"]},
                {"value": "s7_f", "label": "Переговоры между внутренними и внешними сознаниями.", "species_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "Что у тебя вызывает страх?",
            "options": [
                {"value": "s8_a", "label": "Потерять близких и остаться никем не понятым.", "species_tags": ["human"]},
                {"value": "s8_b", "label": "Быть изгнанным из культуры или клана.", "species_tags": ["humanoid"]},
                {"value": "s8_c", "label": "Оказаться в среде, где тело не может функционировать.", "species_tags": ["non_humanoid"]},
                {"value": "s8_d", "label": "Быть запертым в материи и потерять свободу движения.", "species_tags": ["energy"]},
                {"value": "s8_e", "label": "Потерять автономию из-за чужого доступа к системе.", "species_tags": ["cybernetic"]},
                {"value": "s8_f", "label": "Проснуться и понять, что часть тебя больше не отвечает.", "species_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "Что считается красотой?",
            "options": [
                {"value": "s9_a", "label": "Выразительное лицо, голос, несовершенство, живость.", "species_tags": ["human"]},
                {"value": "s9_b", "label": "Гармония формы, традиционный облик, знаки статуса.", "species_tags": ["humanoid"]},
                {"value": "s9_c", "label": "Сложная биология: узор панциря, щупальца, переливы кожи.", "species_tags": ["non_humanoid"]},
                {"value": "s9_d", "label": "Свет, частота, движение, чистота поля.", "species_tags": ["energy"]},
                {"value": "s9_e", "label": "Элегантная конструкция, точность, функциональная симметрия.", "species_tags": ["cybernetic"]},
                {"value": "s9_f", "label": "Совместимость разных существ в одном устойчивом союзе.", "species_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "Что делает личность \"собой\"?",
            "options": [
                {"value": "s10_a", "label": "Выборы, ошибки и отношения.", "species_tags": ["human"]},
                {"value": "s10_b", "label": "Честь, происхождение и место в обществе.", "species_tags": ["humanoid"]},
                {"value": "s10_c", "label": "Форма тела и инстинктивная связь со средой.", "species_tags": ["non_humanoid"]},
                {"value": "s10_d", "label": "Уникальный энергетический паттерн.", "species_tags": ["energy"]},
                {"value": "s10_e", "label": "Неповторимая архитектура данных.", "species_tags": ["cybernetic"]},
                {"value": "s10_f", "label": "Согласие нескольких сущностей быть одним \"я\".", "species_tags": ["symbiotic"]},
            ],
        },
    ],
    LANGUAGE_EN: [
        {
            "text": "What does \"body\" mean to you?",
            "options": [
                {"value": "s1_a", "label": "A biological body — vulnerable, but familiar.", "species_tags": ["human"]},
                {"value": "s1_b", "label": "A body with recognizable anatomy but unusual physiology.", "species_tags": ["humanoid"]},
                {"value": "s1_c", "label": "Any vessel: carapace, tentacles, a crystalline frame, a slime mass.", "species_tags": ["non_humanoid"]},
                {"value": "s1_d", "label": "A temporary field, a cluster, light, plasma, or resonance.", "species_tags": ["energy"]},
                {"value": "s1_e", "label": "A construct that can be repaired, upgraded, and transferred.", "species_tags": ["cybernetic"]},
                {"value": "s1_f", "label": "A union of several beings, where the \"I\" is born between them.", "species_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "How do you perceive death?",
            "options": [
                {"value": "s2_a", "label": "As finitude that gives life meaning.", "species_tags": ["human"]},
                {"value": "s2_b", "label": "As a biological stage surrounded by rituals.", "species_tags": ["humanoid"]},
                {"value": "s2_c", "label": "As a change of state: molting, decay, hibernation, regeneration.", "species_tags": ["non_humanoid"]},
                {"value": "s2_d", "label": "As energy dispersing into a wider field.", "species_tags": ["energy"]},
                {"value": "s2_e", "label": "As data loss that can be partially prevented.", "species_tags": ["cybernetic"]},
                {"value": "s2_f", "label": "As a severed link between memory carriers.", "species_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "What is considered intimacy among your people?",
            "options": [
                {"value": "s3_a", "label": "An honest face-to-face conversation.", "species_tags": ["human"]},
                {"value": "s3_b", "label": "Touch, gesture, ritual closeness.", "species_tags": ["humanoid"]},
                {"value": "s3_c", "label": "Exchange of scents, biochemistry, vibrations, or pheromones.", "species_tags": ["non_humanoid"]},
                {"value": "s3_d", "label": "Frequency synchronization or aura merging.", "species_tags": ["energy"]},
                {"value": "s3_e", "label": "Access to a private memory archive.", "species_tags": ["cybernetic"]},
                {"value": "s3_f", "label": "Temporary splitting of consciousness with another being.", "species_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "How does your species learn?",
            "options": [
                {"value": "s4_a", "label": "Through experience, mistakes, and mentors.", "species_tags": ["human"]},
                {"value": "s4_b", "label": "Through schools, traditions, and discipline.", "species_tags": ["humanoid"]},
                {"value": "s4_c", "label": "Through instinctive cycles, mutations, or the environment.", "species_tags": ["non_humanoid"]},
                {"value": "s4_d", "label": "Through absorbing patterns, waves, and emotional traces.", "species_tags": ["energy"]},
                {"value": "s4_e", "label": "Through uploading, updates, and optimization.", "species_tags": ["cybernetic"]},
                {"value": "s4_f", "label": "Through inherited memory of past carriers.", "species_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "What kind of home feels natural to you?",
            "options": [
                {"value": "s5_a", "label": "A room with personal belongings and memories.", "species_tags": ["human"]},
                {"value": "s5_b", "label": "A city, temple, ship, or family clan.", "species_tags": ["humanoid"]},
                {"value": "s5_c", "label": "A nest, reef, cave, gas layer, subglacial ocean.", "species_tags": ["non_humanoid"]},
                {"value": "s5_d", "label": "A stellar corona, nebula, electromagnetic storm.", "species_tags": ["energy"]},
                {"value": "s5_e", "label": "A station, server node, repair dock, modular ship.", "species_tags": ["cybernetic"]},
                {"value": "s5_f", "label": "A place where one can safely connect with other organisms.", "species_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "What is memory to you?",
            "options": [
                {"value": "s6_a", "label": "Personal memories that can become distorted.", "species_tags": ["human"]},
                {"value": "s6_b", "label": "Ancestral history and cultural continuity.", "species_tags": ["humanoid"]},
                {"value": "s6_c", "label": "Traces in the body: scars, chemical changes, instincts.", "species_tags": ["non_humanoid"]},
                {"value": "s6_d", "label": "A resonance left in space.", "species_tags": ["energy"]},
                {"value": "s6_e", "label": "An archive, a backup, an event log.", "species_tags": ["cybernetic"]},
                {"value": "s6_f", "label": "A living chain of memories passed through union.", "species_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "How does your species resolve conflicts?",
            "options": [
                {"value": "s7_a", "label": "Debate, compromise, voting, sometimes a fight.", "species_tags": ["human"]},
                {"value": "s7_b", "label": "Council of elders, honor code, diplomatic ritual.", "species_tags": ["humanoid"]},
                {"value": "s7_c", "label": "Display of strength, color, scent, size, or venom.", "species_tags": ["non_humanoid"]},
                {"value": "s7_d", "label": "Frequency shift, field splitting, emotional resonance.", "species_tags": ["energy"]},
                {"value": "s7_e", "label": "Protocol reflash, logical arbitration.", "species_tags": ["cybernetic"]},
                {"value": "s7_f", "label": "Negotiations between inner and outer consciousnesses.", "species_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "What frightens you?",
            "options": [
                {"value": "s8_a", "label": "Losing loved ones and remaining misunderstood.", "species_tags": ["human"]},
                {"value": "s8_b", "label": "Being exiled from culture or clan.", "species_tags": ["humanoid"]},
                {"value": "s8_c", "label": "Ending up in an environment where the body cannot function.", "species_tags": ["non_humanoid"]},
                {"value": "s8_d", "label": "Being trapped in matter and losing freedom of movement.", "species_tags": ["energy"]},
                {"value": "s8_e", "label": "Losing autonomy due to unauthorized system access.", "species_tags": ["cybernetic"]},
                {"value": "s8_f", "label": "Waking up to find part of you no longer responds.", "species_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "What is considered beautiful?",
            "options": [
                {"value": "s9_a", "label": "An expressive face, a voice, imperfection, liveliness.", "species_tags": ["human"]},
                {"value": "s9_b", "label": "Harmony of form, traditional appearance, status markers.", "species_tags": ["humanoid"]},
                {"value": "s9_c", "label": "Complex biology: carapace pattern, tentacles, skin iridescence.", "species_tags": ["non_humanoid"]},
                {"value": "s9_d", "label": "Light, frequency, motion, field purity.", "species_tags": ["energy"]},
                {"value": "s9_e", "label": "Elegant construction, precision, functional symmetry.", "species_tags": ["cybernetic"]},
                {"value": "s9_f", "label": "Compatibility of different beings in one stable union.", "species_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "What makes a person \"themselves\"?",
            "options": [
                {"value": "s10_a", "label": "Choices, mistakes, and relationships.", "species_tags": ["human"]},
                {"value": "s10_b", "label": "Honor, lineage, and place in society.", "species_tags": ["humanoid"]},
                {"value": "s10_c", "label": "Body shape and instinctive connection to the environment.", "species_tags": ["non_humanoid"]},
                {"value": "s10_d", "label": "A unique energy pattern.", "species_tags": ["energy"]},
                {"value": "s10_e", "label": "A unique data architecture.", "species_tags": ["cybernetic"]},
                {"value": "s10_f", "label": "The agreement of multiple entities to be one \"I\".", "species_tags": ["symbiotic"]},
            ],
        },
    ],
}

# Gender onboarding questions (4 questions)
GENDER_QUESTIONS_DATA = {
    LANGUAGE_RU: [
        {
            "text": "Как твой вид участвует в продолжении рода?",
            "options": [
                {"value": "g1_a", "label": "Через мужскую репродуктивную роль.", "gender_tags": ["male"]},
                {"value": "g1_b", "label": "Через женскую репродуктивную роль.", "gender_tags": ["female"]},
                {"value": "g1_c", "label": "Индивидуум не участвует в размножении напрямую.", "gender_tags": ["neutral"]},
                {"value": "g1_d", "label": "Роль меняется в течение жизни.", "gender_tags": ["fluid"]},
                {"value": "g1_e", "label": "Для рождения нужно больше двух половых ролей.", "gender_tags": ["multiple"]},
                {"value": "g1_f", "label": "Потомство возникает через слияние энергетических паттернов.", "gender_tags": ["resonance"]},
                {"value": "g1_g", "label": "Пол задан конструкцией, модулем или протоколом.", "gender_tags": ["synthetic"]},
                {"value": "g1_h", "label": "Пол возникает только в союзе носителя и симбионта.", "gender_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "Как общество обращается к тебе?",
            "options": [
                {"value": "g2_a", "label": "Как к мужчине.", "gender_tags": ["male"]},
                {"value": "g2_b", "label": "Как к женщине.", "gender_tags": ["female"]},
                {"value": "g2_c", "label": "Без половых обращений.", "gender_tags": ["neutral"]},
                {"value": "g2_d", "label": "По текущей фазе жизни.", "gender_tags": ["fluid"]},
                {"value": "g2_e", "label": "По одной из нескольких половых функций.", "gender_tags": ["multiple"]},
                {"value": "g2_f", "label": "По частоте, тону или световому спектру.", "gender_tags": ["resonance"]},
                {"value": "g2_g", "label": "По серийному, функциональному или выбранному обозначению.", "gender_tags": ["synthetic"]},
                {"value": "g2_h", "label": "По имени союза, а не отдельного тела.", "gender_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "Что значит \"зрелость\" для твоего вида?",
            "options": [
                {"value": "g3_a", "label": "Физическая и социальная зрелость взрослого мужчины.", "gender_tags": ["male"]},
                {"value": "g3_b", "label": "Физическая и социальная зрелость взрослой женщины.", "gender_tags": ["female"]},
                {"value": "g3_c", "label": "Выход за пределы репродуктивной функции.", "gender_tags": ["neutral"]},
                {"value": "g3_d", "label": "Переход в новую половую фазу.", "gender_tags": ["fluid"]},
                {"value": "g3_e", "label": "Получение доступа к нескольким репродуктивным ролям.", "gender_tags": ["multiple"]},
                {"value": "g3_f", "label": "Стабилизация личной частоты.", "gender_tags": ["resonance"]},
                {"value": "g3_g", "label": "Завершение сборки или самоопределение конструкции.", "gender_tags": ["synthetic"]},
                {"value": "g3_h", "label": "Первое успешное соединение с другим существом.", "gender_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "Как ты описал бы себя врачу Звёздного флота?",
            "options": [
                {"value": "g4_a", "label": "\"Мужская биология, стандартные отклонения в пределах нормы.\"", "gender_tags": ["male"]},
                {"value": "g4_b", "label": "\"Женская биология, стандартные отклонения в пределах нормы.\"", "gender_tags": ["female"]},
                {"value": "g4_c", "label": "\"Репродуктивные органы отсутствуют или неактивны.\"", "gender_tags": ["neutral"]},
                {"value": "g4_d", "label": "\"Моя биология меняется циклически.\"", "gender_tags": ["fluid"]},
                {"value": "g4_e", "label": "\"Мой вид имеет более двух половых функций.\"", "gender_tags": ["multiple"]},
                {"value": "g4_f", "label": "\"Мой пол определяется энергетическим резонансом.\"", "gender_tags": ["resonance"]},
                {"value": "g4_g", "label": "\"Мой пол — конструкционный параметр, а не биология.\"", "gender_tags": ["synthetic"]},
                {"value": "g4_h", "label": "\"Мой пол нельзя описать без моего симбионта/партнёра.\"", "gender_tags": ["symbiotic"]},
            ],
        },
    ],
    LANGUAGE_EN: [
        {
            "text": "How does your species participate in reproduction?",
            "options": [
                {"value": "g1_a", "label": "Through a male reproductive role.", "gender_tags": ["male"]},
                {"value": "g1_b", "label": "Through a female reproductive role.", "gender_tags": ["female"]},
                {"value": "g1_c", "label": "The individual does not participate in reproduction directly.", "gender_tags": ["neutral"]},
                {"value": "g1_d", "label": "The role changes throughout life.", "gender_tags": ["fluid"]},
                {"value": "g1_e", "label": "More than two reproductive roles are needed for birth.", "gender_tags": ["multiple"]},
                {"value": "g1_f", "label": "Offspring arise through merging of energy patterns.", "gender_tags": ["resonance"]},
                {"value": "g1_g", "label": "Gender is defined by construction, module, or protocol.", "gender_tags": ["synthetic"]},
                {"value": "g1_h", "label": "Gender only exists in a union of host and symbiont.", "gender_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "How does society address you?",
            "options": [
                {"value": "g2_a", "label": "As a man.", "gender_tags": ["male"]},
                {"value": "g2_b", "label": "As a woman.", "gender_tags": ["female"]},
                {"value": "g2_c", "label": "Without gendered references.", "gender_tags": ["neutral"]},
                {"value": "g2_d", "label": "By current life phase.", "gender_tags": ["fluid"]},
                {"value": "g2_e", "label": "By one of several reproductive functions.", "gender_tags": ["multiple"]},
                {"value": "g2_f", "label": "By frequency, tone, or light spectrum.", "gender_tags": ["resonance"]},
                {"value": "g2_g", "label": "By serial, functional, or chosen designation.", "gender_tags": ["synthetic"]},
                {"value": "g2_h", "label": "By the union's name, not the individual body.", "gender_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "What does \"maturity\" mean for your species?",
            "options": [
                {"value": "g3_a", "label": "Physical and social maturity of an adult male.", "gender_tags": ["male"]},
                {"value": "g3_b", "label": "Physical and social maturity of an adult female.", "gender_tags": ["female"]},
                {"value": "g3_c", "label": "Moving beyond reproductive function.", "gender_tags": ["neutral"]},
                {"value": "g3_d", "label": "Transition to a new gender phase.", "gender_tags": ["fluid"]},
                {"value": "g3_e", "label": "Gaining access to multiple reproductive roles.", "gender_tags": ["multiple"]},
                {"value": "g3_f", "label": "Stabilization of one's personal frequency.", "gender_tags": ["resonance"]},
                {"value": "g3_g", "label": "Completion of assembly or self-determination of the construct.", "gender_tags": ["synthetic"]},
                {"value": "g3_h", "label": "First successful connection with another being.", "gender_tags": ["symbiotic"]},
            ],
        },
        {
            "text": "How would you describe yourself to a Starfleet doctor?",
            "options": [
                {"value": "g4_a", "label": "\"Male biology, standard deviations within normal limits.\"", "gender_tags": ["male"]},
                {"value": "g4_b", "label": "\"Female biology, standard deviations within normal limits.\"", "gender_tags": ["female"]},
                {"value": "g4_c", "label": "\"Reproductive organs absent or inactive.\"", "gender_tags": ["neutral"]},
                {"value": "g4_d", "label": "\"My biology changes cyclically.\"", "gender_tags": ["fluid"]},
                {"value": "g4_e", "label": "\"My species has more than two reproductive functions.\"", "gender_tags": ["multiple"]},
                {"value": "g4_f", "label": "\"My gender is determined by energy resonance.\"", "gender_tags": ["resonance"]},
                {"value": "g4_g", "label": "\"My gender is a construction parameter, not biology.\"", "gender_tags": ["synthetic"]},
                {"value": "g4_h", "label": "\"My gender cannot be described without my symbiont/partner.\"", "gender_tags": ["symbiotic"]},
            ],
        },
    ],
}


def get_ship_role_i18n(role_key: str, language: str = LANGUAGE_RU) -> dict:
    """Get localized ship role data by role_key and language.

    Returns dict with keys: role_name, role_description, avatar_description, personality_traits.
    Falls back to Russian if language not found.
    """
    role_data = SHIP_ROLES_I18N.get(role_key, {})
    return role_data.get(language, role_data.get(LANGUAGE_RU, {}))


def get_all_ship_roles_i18n(language: str = LANGUAGE_RU) -> dict:
    """Get all ship roles for a specific language.

    Returns dict keyed by role_key, each value containing
    role_name, role_description, avatar_description, personality_traits.
    """
    return {
        role_key: data.get(language, data.get(LANGUAGE_RU, {}))
        for role_key, data in SHIP_ROLES_I18N.items()
    }


def get_species_type_name(species_type: str, language: str = LANGUAGE_RU) -> str:
    """Get localized species type display name."""
    names = SPECIES_TYPE_NAMES.get(language, SPECIES_TYPE_NAMES[LANGUAGE_RU])
    return names.get(species_type, species_type)


def get_hybrid_species_name(hybrid_key: str, language: str = LANGUAGE_RU) -> str:
    """Get localized hybrid species description."""
    names = HYBRID_SPECIES_NAMES.get(language, HYBRID_SPECIES_NAMES[LANGUAGE_RU])
    return names.get(hybrid_key, hybrid_key)


def get_gender_type_name(gender_type: str, language: str = LANGUAGE_RU) -> str:
    """Get localized gender type display name."""
    names = GENDER_TYPE_NAMES.get(language, GENDER_TYPE_NAMES[LANGUAGE_RU])
    return names.get(gender_type, gender_type)


def get_species_questions_data(language: str = LANGUAGE_RU) -> list:
    """Get species onboarding questions for a specific language."""
    return SPECIES_QUESTIONS_DATA.get(language, SPECIES_QUESTIONS_DATA[LANGUAGE_RU])


def get_gender_questions_data(language: str = LANGUAGE_RU) -> list:
    """Get gender onboarding questions for a specific language."""
    return GENDER_QUESTIONS_DATA.get(language, GENDER_QUESTIONS_DATA[LANGUAGE_RU])
