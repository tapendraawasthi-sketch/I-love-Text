// Standard Preeti to Unicode Conversion mapping
const PREETI_TO_UNICODE = {
    // Array 1: Preeti Character sequence
    preeti: [
        "ç","˜",".","'","m","]","F","!","¡","¢","£","¤","¥","§","¨","©","ª","«","¬","®","¯","°","±","²","³","´","µ","¶","·","¸","¹","º","»","¼","½","¾","¿","À","Á","Â","Ã","Ä","Å","Æ","Ç","È","É","Ê","Ë","Ì","Í","Î","Ï","Ð","Ñ","Ò","Ó","Ô","Õ","Ö","×","Ø","Ù","Ú","Û","Ü","Ý","Þ","ß","à","á","â","ã","ä","å","æ","ç","è","é","ê","ë","ì","í","î","ï","ð","ñ","ò","ó","ô","õ","ö","÷","ø","ù","ú","û","ü","ý","þ","ÿ","s","v","z","W","X","w","x","y","k","g","d","j","a","l","h","u","i","e","o","p","A","B","C","D","E","q","r","t","y","V","U","I","O","P","Z","x","c","b","n","m","M","<",">","?","L","K","J","H","G","F","S","D","f","1","2","3","4","5","6","7","8","9","0","!","@","#","$","%","^","&","*","(",")"
    ],
    // Array 2: Unicode character equivalent
    unicode: [
        "ॐ","ऽ","।","’","m","e","ँ","१","२","३","४","५","६","७","८","९","०","र्","त्त","त्र","त्त्","श्र","श्च","ष्ट","ष्ठ","श्व","स्न","त्र","ल्ल","ल्द","ष्ट","ष","त्त","प्त","ङ्ग","ञ्च","हृ","द्व","ह्य","ट्ट","ट्ठ","ड्ड","ड्ढ","ठ्ठ","ह्ल","ह्व","झ्र","स्र","ज्ञ","ज्ञ्","घ्","घ","छ","छ्","ड्ड","ट्ठ","ठ्ठ","रू","हृ","ष्ट","क्ष्","क्ष","च्च","च्म","त्त्","न्न्","त्र","प्त","ल्ल्","प्ल","त्र्","ष्ट","ङ्ख","ङ्घ","ड्य","ह्र","द्व","ह्य","ट्ट","ट्ठ","ड्ड","ड्ढ","ठ्ठ","ह्ल","ह्व","झ्र","स्र","ज्ञ","ज्ञ्","घ्","घ","छ","छ्","ड्ड","ट्ठ","ठ्ठ","रू","हृ","ष्ट","क्ष्","क्ष","च्च","च्म","त्त्","न्न्","त्र","प्त","ल्ल्","प्ल","त्र्","क","ख","ग","घ","ङ","च","छ","ज","झ","ञ","ट","ठ","ड","ढ","ण","त","थ","द","ध","न","प","फ","ब","भ","म","य","र","ल","व","श","ष","स","ह","क्ष","त्र","ज्ञ","१","२","३","४","५","६","७","८","९","०","!","@","#","$","%","^","&","*","(",")"
    ]
};

function preetiToUnicode(text) {
    if (!text) return text;
    let unicodeText = text;

    for (let i = 0; i < PREETI_TO_UNICODE.preeti.length; i++) {
        // Need to escape regex special characters in Preeti string
        const escapeRegExp = (string) => string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const search = escapeRegExp(PREETI_TO_UNICODE.preeti[i]);
        const replace = PREETI_TO_UNICODE.unicode[i];
        unicodeText = unicodeText.replace(new RegExp(search, 'g'), replace);
    }
    
    // Fix modifier placement (Preeti stores short 'i' before consonant)
    unicodeText = unicodeText.replace(/ि([क-ह])/g, "$1ि");
    
    return unicodeText;
}

// Simple heuristic to check if a string is likely Preeti (high density of specific English ASCII)
function isLikelyPreeti(text) {
    if (!text || text.length < 10) return false;
    
    // Preeti texts look like random english. If we see common Preeti clusters:
    const preetiIndicators = ["cf", "sf", "of", "df", "jf", "tf", "xf", "k|", "O{", "Uf", "If", "sfo{"];
    let score = 0;
    preetiIndicators.forEach(ind => {
        if (text.includes(ind)) score += 2;
    });
    
    return score >= 2;
}

window.preetiToUnicode = preetiToUnicode;
window.isLikelyPreeti = isLikelyPreeti;
