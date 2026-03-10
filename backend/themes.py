# themes.py
import random
import re

# Theme categories and their keywords for matching
THEME_CATEGORIES = {
    "modern": {
        "keywords": ["modern", "contemporary", "sleek", "minimal", "clean"],
        "description": "Clean lines, ample white space, neutral colors with accent"
    },
    "dark": {
        "keywords": ["dark", "night", "midnight", "black"],
        "description": "Dark backgrounds, high contrast, vibrant accents"
    },
    "nature": {
        "keywords": ["nature", "organic", "green", "earthy", "forest", "plant"],
        "description": "Earthy tones, greens, browns, soft textures"
    },
    "corporate": {
        "keywords": ["corporate", "business", "professional", "enterprise"],
        "description": "Blues, grays, structured layouts, conservative"
    },
    "playful": {
        "keywords": ["playful", "fun", "colorful", "kids", "creative"],
        "description": "Bright colors, rounded corners, whimsical elements"
    },
    "luxury": {
        "keywords": ["luxury", "elegant", "sophisticated", "premium", "high-end"],
        "description": "Gold, deep purples, serif fonts, subtle gradients"
    },
    "tech": {
        "keywords": ["tech", "technology", "startup", "software", "digital"],
        "description": "Blues, cyans, gradients, futuristic elements"
    },
    "minimal": {
        "keywords": ["minimal", "simple", "bare", "essential"],
        "description": "Monochrome, lots of whitespace, no frills"
    }
}

# Actual theme definitions
THEMES = [
    {
        "name": "Ocean Breeze",
        "category": "modern",
        "colors": ["#2B6C94", "#4A9FD8", "#FFFFFF", "#F5F5F5", "#333333"],
        "animation": "fade",
        "description": "Calm blues with white space"
    },
    {
        "name": "Midnight",
        "category": "dark",
        "colors": ["#121212", "#1E1E1E", "#BB86FC", "#03DAC6", "#FFFFFF"],
        "animation": "slide",
        "description": "Dark background with purple and teal accents"
    },
    {
        "name": "Forest",
        "category": "nature",
        "colors": ["#2C5F2D", "#97BC62", "#F5F5F5", "#8B5A2B", "#2B2B2B"],
        "animation": "fade",
        "description": "Greens and browns, earthy feel"
    },
    {
        "name": "Corporate Blue",
        "category": "corporate",
        "colors": ["#005A9C", "#0078D7", "#FFFFFF", "#F0F0F0", "#333333"],
        "animation": "none",
        "description": "Trustworthy blues and grays"
    },
    {
        "name": "Sunset",
        "category": "playful",
        "colors": ["#FF6B6B", "#FFD93D", "#6BCB77", "#4D96FF", "#FFFFFF"],
        "animation": "bounce",
        "description": "Warm, energetic colors"
    },
    {
        "name": "Royal",
        "category": "luxury",
        "colors": ["#4B0082", "#8A2BE2", "#DAA520", "#F8F8FF", "#2C2C2C"],
        "animation": "fade",
        "description": "Deep purples with gold accents"
    },
    {
        "name": "Neon",
        "category": "tech",
        "colors": ["#0D0D0D", "#00FF9F", "#00B8FF", "#FF00E5", "#FFFFFF"],
        "animation": "glow",
        "description": "Dark background with vibrant neon accents"
    },
    {
        "name": "Zen",
        "category": "minimal",
        "colors": ["#FFFFFF", "#F5F5F5", "#E0E0E0", "#9E9E9E", "#212121"],
        "animation": "none",
        "description": "Pure minimalism, shades of gray"
    },
    {
        "name": "Tropical",
        "category": "playful",
        "colors": ["#FF9A8B", "#FF6A88", "#FF99AC", "#FFD6E0", "#FFFFFF"],
        "animation": "slide",
        "description": "Pinks and corals, vibrant"
    },
    {
        "name": "Earth",
        "category": "nature",
        "colors": ["#A79B82", "#7A6A53", "#D4B996", "#F5F0E6", "#3A3A3A"],
        "animation": "fade",
        "description": "Neutral earth tones"
    },
    {
        "name": "Deep Space",
        "category": "dark",
        "colors": ["#0B0C10", "#1F2833", "#66FCF1", "#45A29E", "#C5C6C7"],
        "animation": "glow",
        "description": "Dark with teal accents"
    },
    {
        "name": "Elegant",
        "category": "luxury",
        "colors": ["#1A1A2E", "#16213E", "#0F3460", "#E94560", "#F1F1F1"],
        "animation": "fade",
        "description": "Deep blue with red accent"
    },
    # Modern (13 themes)
    {
        "name": "Ocean Breeze",
        "category": "modern",
        "colors": ["#2B6C94", "#4A9FD8", "#FFFFFF", "#F5F5F5", "#333333"],
        "animation": "fade",
        "description": "Calm blues with white space"
    },
    {
        "name": "Urban Loft",
        "category": "modern",
        "colors": ["#2C3E50", "#E67E22", "#ECF0F1", "#BDC3C7", "#34495E"],
        "animation": "slide",
        "description": "Industrial gray with orange accent"
    },
    {
        "name": "Glass",
        "category": "modern",
        "colors": ["#F8F9FA", "#E9ECEF", "#DEE2E6", "#495057", "#0D6EFD"],
        "animation": "fade",
        "description": "Frosted glass effect with light blue"
    },
    {
        "name": "Monochrome",
        "category": "modern",
        "colors": ["#FFFFFF", "#F2F2F2", "#CCCCCC", "#666666", "#222222"],
        "animation": "none",
        "description": "Pure black and white minimalism"
    },
    {
        "name": "Nordic",
        "category": "modern",
        "colors": ["#F0F0F0", "#D9E5D6", "#A7C4B5", "#4A6C6F", "#1E2F3A"],
        "animation": "fade",
        "description": "Scandinavian pastels and calm"
    },
    {
        "name": "Metro",
        "category": "modern",
        "colors": ["#F1F1F1", "#7F8C8D", "#3498DB", "#2C3E50", "#ECF0F1"],
        "animation": "slide",
        "description": "Clean Windows Metro style"
    },
    {
        "name": "Airy",
        "category": "modern",
        "colors": ["#E3F2FD", "#BBDEFB", "#90CAF9", "#1976D2", "#0D47A1"],
        "animation": "fade",
        "description": "Light and airy blues"
    },
    {
        "name": "Concrete",
        "category": "modern",
        "colors": ["#B0BEC5", "#78909C", "#546E7A", "#37474F", "#263238"],
        "animation": "none",
        "description": "Brutalist concrete grays"
    },
    {
        "name": "Pastel",
        "category": "modern",
        "colors": ["#FFB3BA", "#FFDFBA", "#FFFFBA", "#BAFFC9", "#BAE1FF"],
        "animation": "bounce",
        "description": "Soft pastel rainbow"
    },
    {
        "name": "Neo-Minimal",
        "category": "modern",
        "colors": ["#EDEDED", "#D6D6D6", "#F25F5C", "#50514F", "#247BA0"],
        "animation": "fade",
        "description": "Minimal with a pop of red"
    },
    {
        "name": "Cloud",
        "category": "modern",
        "colors": ["#DFE7F2", "#B5C8E0", "#7F9BC0", "#4F6D8F", "#2A4059"],
        "animation": "fade",
        "description": "Soft cloud-like blues"
    },
    {
        "name": "Slate",
        "category": "modern",
        "colors": ["#708090", "#5A6A7A", "#445565", "#2F3F4F", "#1A2A3A"],
        "animation": "none",
        "description": "Deep slate grays"
    },
    {
        "name": "Ink",
        "category": "modern",
        "colors": ["#F5F5F5", "#E0E0E0", "#9E9E9E", "#607D8B", "#263238"],
        "animation": "fade",
        "description": "Ink on paper"
    },

    # Dark (13 themes)
    {
        "name": "Midnight",
        "category": "dark",
        "colors": ["#121212", "#1E1E1E", "#BB86FC", "#03DAC6", "#FFFFFF"],
        "animation": "slide",
        "description": "Dark background with purple and teal accents"
    },
    {
        "name": "Deep Space",
        "category": "dark",
        "colors": ["#0B0C10", "#1F2833", "#66FCF1", "#45A29E", "#C5C6C7"],
        "animation": "glow",
        "description": "Dark with teal accents"
    },
    {
        "name": "Void",
        "category": "dark",
        "colors": ["#000000", "#0A0A0A", "#8257E6", "#04D361", "#E1E1E6"],
        "animation": "fade",
        "description": "Pure black with purple and green"
    },
    {
        "name": "Cyberpunk",
        "category": "dark",
        "colors": ["#0D0221", "#240B36", "#6A0572", "#FF2E63", "#EAEAEA"],
        "animation": "glow",
        "description": "Deep purples with hot pink"
    },
    {
        "name": "Onyx",
        "category": "dark",
        "colors": ["#1A1A1A", "#2A2A2A", "#C0B9B0", "#8A7F70", "#F5F5F5"],
        "animation": "none",
        "description": "Black and stone accents"
    },
    {
        "name": "Nightfall",
        "category": "dark",
        "colors": ["#0A1929", "#1A2A3A", "#2A3A4A", "#E5B8F4", "#FFFFFF"],
        "animation": "slide",
        "description": "Dark blue with lavender"
    },
    {
        "name": "Obsidian",
        "category": "dark",
        "colors": ["#161618", "#2D2D30", "#3E3E42", "#007ACC", "#F0F0F0"],
        "animation": "fade",
        "description": "Dark editor theme with blue"
    },
    {
        "name": "Blood Moon",
        "category": "dark",
        "colors": ["#1E0B0B", "#3C1A1A", "#5A2929", "#D4AF37", "#F0E68C"],
        "animation": "bounce",
        "description": "Dark reds with gold"
    },
    {
        "name": "Abyss",
        "category": "dark",
        "colors": ["#000814", "#001D3D", "#003566", "#FFC300", "#FFD60A"],
        "animation": "glow",
        "description": "Deep ocean with yellow"
    },
    {
        "name": "Charcoal",
        "category": "dark",
        "colors": ["#282C35", "#3E4451", "#5C6370", "#E5C07B", "#98C379"],
        "animation": "none",
        "description": "Charcoal with warm code syntax"
    },
    {
        "name": "Eclipse",
        "category": "dark",
        "colors": ["#1E1E2E", "#2E2E3E", "#3E3E4E", "#CBA6F7", "#F5E0DC"],
        "animation": "fade",
        "description": "Dark mauve with lavender"
    },
    {
        "name": "Shadow",
        "category": "dark",
        "colors": ["#0F0F0F", "#1F1F1F", "#2F2F2F", "#FFB347", "#FFFFFF"],
        "animation": "slide",
        "description": "Grayscale with orange accent"
    },
    {
        "name": "Galaxy",
        "category": "dark",
        "colors": ["#0B0719", "#1C1A3A", "#3D2B56", "#9B6B9B", "#F2D0F2"],
        "animation": "glow",
        "description": "Purples and pinks like nebula"
    },

    # Nature (13 themes)
    {
        "name": "Forest",
        "category": "nature",
        "colors": ["#2C5F2D", "#97BC62", "#F5F5F5", "#8B5A2B", "#2B2B2B"],
        "animation": "fade",
        "description": "Greens and browns, earthy feel"
    },
    {
        "name": "Earth",
        "category": "nature",
        "colors": ["#A79B82", "#7A6A53", "#D4B996", "#F5F0E6", "#3A3A3A"],
        "animation": "fade",
        "description": "Neutral earth tones"
    },
    {
        "name": "Meadow",
        "category": "nature",
        "colors": ["#7CB342", "#5E8C3E", "#FFD54F", "#FFF9C4", "#3E4A3D"],
        "animation": "bounce",
        "description": "Grass green with yellow flowers"
    },
    {
        "name": "Ocean Deep",
        "category": "nature",
        "colors": ["#01579B", "#0277BD", "#0288D1", "#B3E5FC", "#E1F5FE"],
        "animation": "fade",
        "description": "Deep sea blues"
    },
    {
        "name": "Desert",
        "category": "nature",
        "colors": ["#EDC9AF", "#DEB887", "#C4A484", "#8B5A2B", "#5C4033"],
        "animation": "slide",
        "description": "Sandy tones and browns"
    },
    {
        "name": "Moss",
        "category": "nature",
        "colors": ["#4A5D23", "#6A7E3A", "#8A9E5A", "#B2B48C", "#2E3B1F"],
        "animation": "fade",
        "description": "Soft mossy greens"
    },
    {
        "name": "Sunset",
        "category": "nature",
        "colors": ["#FF6F61", "#FF9A5A", "#FFD166", "#6B4F3C", "#2E3B4E"],
        "animation": "glow",
        "description": "Warm sunset oranges"
    },
    {
        "name": "Arctic",
        "category": "nature",
        "colors": ["#D6EAF8", "#AED6F1", "#85C1E2", "#5DADE2", "#1B4F72"],
        "animation": "none",
        "description": "Icy blues and whites"
    },
    {
        "name": "Autumn",
        "category": "nature",
        "colors": ["#BF5A36", "#A8442F", "#8B3A2B", "#F2C14E", "#4A251C"],
        "animation": "fade",
        "description": "Fall reds and oranges"
    },
    {
        "name": "Spring",
        "category": "nature",
        "colors": ["#F5E6D3", "#C5E0B4", "#9CC184", "#7AA573", "#4F7942"],
        "animation": "bounce",
        "description": "Fresh spring greens"
    },
    {
        "name": "Mountain",
        "category": "nature",
        "colors": ["#6F8C9F", "#4A6670", "#2F4F4F", "#D3C6B0", "#F0EDE5"],
        "animation": "slide",
        "description": "Rocky grays and forest"
    },
    {
        "name": "Tropical",
        "category": "nature",
        "colors": ["#1D5B4A", "#2E8B57", "#66CDAA", "#F4A460", "#FFE4B5"],
        "animation": "bounce",
        "description": "Lush greens and sand"
    },
    {
        "name": "Mushroom",
        "category": "nature",
        "colors": ["#A59E8C", "#7D7565", "#5E5544", "#DCD3C1", "#F3EAD8"],
        "animation": "fade",
        "description": "Earthy mushroom tones"
    },

    # Corporate (13 themes)
    {
        "name": "Corporate Blue",
        "category": "corporate",
        "colors": ["#005A9C", "#0078D7", "#FFFFFF", "#F0F0F0", "#333333"],
        "animation": "none",
        "description": "Trustworthy blues and grays"
    },
    {
        "name": "Executive",
        "category": "corporate",
        "colors": ["#1F2B3A", "#2C3E50", "#34495E", "#BDC3C7", "#ECF0F1"],
        "animation": "none",
        "description": "Dark blues and light grays"
    },
    {
        "name": "Bank",
        "category": "corporate",
        "colors": ["#004B87", "#0072B0", "#6CACE4", "#D9E1E7", "#FFFFFF"],
        "animation": "fade",
        "description": "Conservative financial blues"
    },
    {
        "name": "Law Firm",
        "category": "corporate",
        "colors": ["#3A4E5F", "#4F6F8F", "#6F8F9F", "#CFCFCF", "#2C3E50"],
        "animation": "none",
        "description": "Serious grays and blues"
    },
    {
        "name": "Tech Corporate",
        "category": "corporate",
        "colors": ["#252525", "#4A4A4A", "#6A6A6A", "#00A98F", "#FFFFFF"],
        "animation": "slide",
        "description": "Modern corporate with teal accent"
    },
    {
        "name": "Insurance",
        "category": "corporate",
        "colors": ["#004B8D", "#5C88B5", "#9BC3E6", "#F5F5F5", "#333333"],
        "animation": "fade",
        "description": "Safe and reliable blues"
    },
    {
        "name": "Consulting",
        "category": "corporate",
        "colors": ["#5A5A5A", "#7A7A7A", "#9A9A9A", "#C49A6C", "#F5F5F5"],
        "animation": "none",
        "description": "Neutral grays with gold accent"
    },
    {
        "name": "Enterprise",
        "category": "corporate",
        "colors": ["#0E3B5E", "#1C527B", "#2A6998", "#B0C4DE", "#F0F8FF"],
        "animation": "fade",
        "description": "Deep blues and light blue"
    },
    {
        "name": "Accounting",
        "category": "corporate",
        "colors": ["#2D5A27", "#4F7942", "#7CA87C", "#E2E6E2", "#FFFFFF"],
        "animation": "none",
        "description": "Greens representing growth"
    },
    {
        "name": "Legal",
        "category": "corporate",
        "colors": ["#4B3B40", "#6B5B60", "#8B7B80", "#B7AEB1", "#E8E2E4"],
        "animation": "fade",
        "description": "Sophisticated muted purples"
    },
    {
        "name": "Startup",
        "category": "corporate",
        "colors": ["#1A1A1A", "#333333", "#4D4D4D", "#00CEC9", "#FFFFFF"],
        "animation": "bounce",
        "description": "Dark with vibrant cyan"
    },
    {
        "name": "Nonprofit",
        "category": "corporate",
        "colors": ["#006B6B", "#008B8B", "#20B2AA", "#F0F0F0", "#2F4F4F"],
        "animation": "fade",
        "description": "Teals and light grays"
    },
    {
        "name": "Government",
        "category": "corporate",
        "colors": ["#002868", "#4C6A9C", "#7F9CC0", "#BF0A30", "#FFFFFF"],
        "animation": "none",
        "description": "Navy, light blue, and red"
    },

    # Playful (13 themes)
    {
        "name": "Sunset",
        "category": "playful",
        "colors": ["#FF6B6B", "#FFD93D", "#6BCB77", "#4D96FF", "#FFFFFF"],
        "animation": "bounce",
        "description": "Warm, energetic colors"
    },
    {
        "name": "Tropical",
        "category": "playful",
        "colors": ["#FF9A8B", "#FF6A88", "#FF99AC", "#FFD6E0", "#FFFFFF"],
        "animation": "slide",
        "description": "Pinks and corals, vibrant"
    },
    {
        "name": "Candy",
        "category": "playful",
        "colors": ["#FFB7B2", "#FF9F9C", "#FFC8A2", "#FFE5B4", "#FFFFFF"],
        "animation": "bounce",
        "description": "Sweet candy pastels"
    },
    {
        "name": "Rainbow",
        "category": "playful",
        "colors": ["#FF0000", "#FF7F00", "#FFFF00", "#00FF00", "#0000FF"],
        "animation": "glow",
        "description": "Full rainbow spectrum"
    },
    {
        "name": "Balloon",
        "category": "playful",
        "colors": ["#F94144", "#F3722C", "#F9C74F", "#90BE6D", "#577590"],
        "animation": "bounce",
        "description": "Bright and cheerful"
    },
    {
        "name": "Lollipop",
        "category": "playful",
        "colors": ["#F28482", "#F5CAC3", "#F7B2B2", "#84A59D", "#F6BD60"],
        "animation": "slide",
        "description": "Soft pinks and mint"
    },
    {
        "name": "Toys",
        "category": "playful",
        "colors": ["#FFD3B5", "#FFB3BA", "#FFDFBA", "#C5E0B4", "#A2D7FC"],
        "animation": "bounce",
        "description": "Primary-like playful pastels"
    },
    {
        "name": "Confetti",
        "category": "playful",
        "colors": ["#FFC75F", "#F9F871", "#D65DB1", "#4D8076", "#FF9671"],
        "animation": "glow",
        "description": "Party confetti colors"
    },
    {
        "name": "Bubblegum",
        "category": "playful",
        "colors": ["#FFC1CC", "#FFB3C6", "#FF9EAA", "#FF85B3", "#FF6F91"],
        "animation": "bounce",
        "description": "Pinks and purples"
    },
    {
        "name": "Kiddie",
        "category": "playful",
        "colors": ["#FAD0C9", "#F6EAC2", "#C1E1C1", "#C2D6E4", "#E0BBE4"],
        "animation": "slide",
        "description": "Soft crayon colors"
    },
    {
        "name": "Summer",
        "category": "playful",
        "colors": ["#FEDB39", "#F5A31A", "#F57C00", "#F44336", "#4FC3F7"],
        "animation": "fade",
        "description": "Bright summer sun and sky"
    },
    {
        "name": "Cartoon",
        "category": "playful",
        "colors": ["#FAC05E", "#F79F79", "#F26868", "#A2D6F9", "#B8B3E9"],
        "animation": "bounce",
        "description": "Bold cartoonish palette"
    },
    {
        "name": "Magic",
        "category": "playful",
        "colors": ["#C5CAE9", "#9FA8DA", "#7986CB", "#5C6BC0", "#3F51B5"],
        "animation": "glow",
        "description": "Purples and lavenders"
    },

    # Luxury (12 themes)
    {
        "name": "Royal",
        "category": "luxury",
        "colors": ["#4B0082", "#8A2BE2", "#DAA520", "#F8F8FF", "#2C2C2C"],
        "animation": "fade",
        "description": "Deep purples with gold accents"
    },
    {
        "name": "Elegant",
        "category": "luxury",
        "colors": ["#1A1A2E", "#16213E", "#0F3460", "#E94560", "#F1F1F1"],
        "animation": "fade",
        "description": "Deep blue with red accent"
    },
    {
        "name": "Platinum",
        "category": "luxury",
        "colors": ["#E5E4E2", "#CFC9C7", "#B9B4B1", "#A39E9B", "#8D8885"],
        "animation": "none",
        "description": "Metallic silvers and grays"
    },
    {
        "name": "Champagne",
        "category": "luxury",
        "colors": ["#F7E7CE", "#F5D7B3", "#F2C8A0", "#D4AF37", "#8A6D3B"],
        "animation": "fade",
        "description": "Soft champagne and gold"
    },
    {
        "name": "Velvet",
        "category": "luxury",
        "colors": ["#2C1A4D", "#3F2A6B", "#523A89", "#C49A6C", "#E5D9B5"],
        "animation": "glow",
        "description": "Rich purples and gold"
    },
    {
        "name": "Onyx & Gold",
        "category": "luxury",
        "colors": ["#0A0A0A", "#1A1A1A", "#2A2A2A", "#CFB53B", "#F0E68C"],
        "animation": "fade",
        "description": "Black with gold accents"
    },
    {
        "name": "Sapphire",
        "category": "luxury",
        "colors": ["#0F4C81", "#1E5F9E", "#2C72BB", "#D4AF37", "#F5F5F5"],
        "animation": "slide",
        "description": "Deep blue sapphire with gold"
    },
    {
        "name": "Ruby",
        "category": "luxury",
        "colors": ["#900C3F", "#B1134B", "#D11A5E", "#F8F0E5", "#3A2610"],
        "animation": "fade",
        "description": "Deep red ruby tones"
    },
    {
        "name": "Pearl",
        "category": "luxury",
        "colors": ["#FDF5E6", "#FAF0DD", "#F5E6D3", "#D4AF37", "#8B6B4D"],
        "animation": "fade",
        "description": "Creamy pearls and gold"
    },
    {
        "name": "Burgundy",
        "category": "luxury",
        "colors": ["#800020", "#9B1D2E", "#B63A4A", "#D4AF37", "#F5F5F5"],
        "animation": "none",
        "description": "Rich burgundy with gold"
    },
    {
        "name": "Ivory",
        "category": "luxury",
        "colors": ["#FFFFF0", "#FDF5E6", "#FAEBD7", "#C0A080", "#8B7355"],
        "animation": "fade",
        "description": "Ivory and antique gold"
    },
    {
        "name": "Noir",
        "category": "luxury",
        "colors": ["#1C1C1C", "#2D2D2D", "#3E3E3E", "#C0A040", "#F0F0F0"],
        "animation": "fade",
        "description": "Black and white with gold"
    },

    # Tech (12 themes)
    {
        "name": "Neon",
        "category": "tech",
        "colors": ["#0D0D0D", "#00FF9F", "#00B8FF", "#FF00E5", "#FFFFFF"],
        "animation": "glow",
        "description": "Dark background with vibrant neon accents"
    },
    {
        "name": "Cyber",
        "category": "tech",
        "colors": ["#000B1A", "#001F3F", "#003366", "#00FFFF", "#FF00FF"],
        "animation": "glow",
        "description": "Deep blue with cyan and magenta"
    },
    {
        "name": "Terminal",
        "category": "tech",
        "colors": ["#0C0C0C", "#1C1C1C", "#00FF00", "#FFFFFF", "#808080"],
        "animation": "fade",
        "description": "Classic green-on-black terminal"
    },
    {
        "name": "Hologram",
        "category": "tech",
        "colors": ["#101720", "#1E2A3A", "#2E3B4E", "#7DF9FF", "#E0E0FF"],
        "animation": "glow",
        "description": "Futuristic holographic blues"
    },
    {
        "name": "Circuit",
        "category": "tech",
        "colors": ["#0A1922", "#1E3440", "#324F5E", "#4C8B9B", "#6EC8D9"],
        "animation": "slide",
        "description": "Circuit board greens and blues"
    },
    {
        "name": "VR",
        "category": "tech",
        "colors": ["#100C1C", "#1F1A2E", "#2E2840", "#A020F0", "#00FFFF"],
        "animation": "glow",
        "description": "Virtual reality purples"
    },
    {
        "name": "Data",
        "category": "tech",
        "colors": ["#131516", "#2C3E50", "#3498DB", "#E74C3C", "#ECF0F1"],
        "animation": "fade",
        "description": "Data visualization colors"
    },
    {
        "name": "AI",
        "category": "tech",
        "colors": ["#1F1F2E", "#2A2A3A", "#353545", "#6C5CE7", "#00CEC9"],
        "animation": "glow",
        "description": "Artificial intelligence purples"
    },
    {
        "name": "Byte",
        "category": "tech",
        "colors": ["#0F0F1F", "#1F1F3F", "#2F2F5F", "#0FF0FF", "#FF0FF0"],
        "animation": "bounce",
        "description": "Binary-inspired neons"
    },
    {
        "name": "Quantum",
        "category": "tech",
        "colors": ["#03001C", "#301E67", "#5B8FB9", "#B6EADA", "#FFFFFF"],
        "animation": "glow",
        "description": "Deep space with quantum glow"
    },
    {
        "name": "Robotics",
        "category": "tech",
        "colors": ["#2A2A2A", "#3A3A3A", "#4A4A4A", "#FF5E5E", "#5EFF5E"],
        "animation": "slide",
        "description": "Metallic grays with red and green"
    },
    {
        "name": "Crypt",
        "category": "tech",
        "colors": ["#0F0F0F", "#1F1F1F", "#2F2F2F", "#F7931A", "#627EEA"],
        "animation": "fade",
        "description": "Bitcoin orange and Ethereum blue"
    },

    # Minimal (11 themes)
    {
        "name": "Zen",
        "category": "minimal",
        "colors": ["#FFFFFF", "#F5F5F5", "#E0E0E0", "#9E9E9E", "#212121"],
        "animation": "none",
        "description": "Pure minimalism, shades of gray"
    },
    {
        "name": "White Space",
        "category": "minimal",
        "colors": ["#FFFFFF", "#FAFAFA", "#F0F0F0", "#E0E0E0", "#BDBDBD"],
        "animation": "none",
        "description": "Lots of white, barely any gray"
    },
    {
        "name": "Paper",
        "category": "minimal",
        "colors": ["#FEFEFA", "#F8F8F8", "#F0F0F0", "#D3D3D3", "#A9A9A9"],
        "animation": "fade",
        "description": "Like unprinted paper"
    },
    {
        "name": "Graphite",
        "category": "minimal",
        "colors": ["#2B2B2B", "#3B3B3B", "#4B4B4B", "#D3D3D3", "#FFFFFF"],
        "animation": "none",
        "description": "Dark minimal with light text"
    },
    {
        "name": "Oatmeal",
        "category": "minimal",
        "colors": ["#F5E9D8", "#E5D9C8", "#D5C9B8", "#B5A99A", "#857A6E"],
        "animation": "fade",
        "description": "Warm neutrals"
    },
    {
        "name": "Chalk",
        "category": "minimal",
        "colors": ["#F9F9F9", "#EFEFEF", "#DFDFDF", "#BFBFBF", "#8F8F8F"],
        "animation": "none",
        "description": "Chalk on slate? Actually light grays"
    },
    {
        "name": "Snow",
        "category": "minimal",
        "colors": ["#F0F8FF", "#E6F0FA", "#DCE8F5", "#C0D0E0", "#A0B0C0"],
        "animation": "fade",
        "description": "Cool off-whites"
    },
    {
        "name": "Limestone",
        "category": "minimal",
        "colors": ["#E8E4D7", "#D8D4C7", "#C8C4B7", "#A8A497", "#888477"],
        "animation": "none",
        "description": "Stony beiges"
    },
    {
        "name": "Fog",
        "category": "minimal",
        "colors": ["#ECECEC", "#DCDCDC", "#CCCCCC", "#ACACAC", "#8C8C8C"],
        "animation": "fade",
        "description": "Misty grays"
    },
    {
        "name": "Parchment",
        "category": "minimal",
        "colors": ["#F1E9D7", "#E1D9C7", "#D1C9B7", "#B1A997", "#918977"],
        "animation": "fade",
        "description": "Old paper tones"
    },
    {
        "name": "Ash",
        "category": "minimal",
        "colors": ["#D0D0D0", "#C0C0C0", "#B0B0B0", "#909090", "#707070"],
        "animation": "none",
        "description": "Light to medium grays"
    },
    {
        "name": "White Label",
        "category": "modern",
        "colors": ["#FFFFFF", "#F2F2F2", "#E0E0E0", "#3A3A3A", "#1A1A1A"],
        "animation": "none",
        "description": "Pure white with subtle grays"
    },
    {
        "name": "Soft Touch",
        "category": "modern",
        "colors": ["#F8F9FA", "#E9ECEF", "#DEE2E6", "#ADB5BD", "#495057"],
        "animation": "fade",
        "description": "Gentle off-whites and grays"
    },
    {
        "name": "Bold Minimal",
        "category": "modern",
        "colors": ["#000000", "#FFFFFF", "#FF3B3F", "#CAEBF2", "#A9A9A9"],
        "animation": "slide",
        "description": "Black and white with red accent"
    },
    {
        "name": "Coastal",
        "category": "modern",
        "colors": ["#D9E5D6", "#B5D3E7", "#9AB9D4", "#7FA5C2", "#3B7A9E"],
        "animation": "fade",
        "description": "Seaside blues and greens"
    },
    {
        "name": "Urban",
        "category": "modern",
        "colors": ["#4A4E5C", "#6A6F7F", "#8A8FA2", "#C0C5D9", "#E8ECF2"],
        "animation": "none",
        "description": "Cityscape grays"
    },
    {
        "name": "Clay",
        "category": "modern",
        "colors": ["#E1B07E", "#C38E70", "#A5684A", "#774E3E", "#3B2E24"],
        "animation": "fade",
        "description": "Warm terracotta and clay"
    },
    {
        "name": "Ice",
        "category": "modern",
        "colors": ["#E0F2FE", "#BAE6FD", "#7DD3FC", "#38BDF8", "#0284C7"],
        "animation": "glow",
        "description": "Cool icy blues"
    },
    {
        "name": "Almond",
        "category": "modern",
        "colors": ["#FFEBCD", "#FFDAB9", "#FBCEB1", "#E3A87C", "#C6865A"],
        "animation": "fade",
        "description": "Nutty warm neutrals"
    },
    {
        "name": "Graphite Light",
        "category": "modern",
        "colors": ["#D9D9D9", "#BFBFBF", "#A6A6A6", "#8C8C8C", "#404040"],
        "animation": "none",
        "description": "Light graphite grays"
    },
    {
        "name": "Powder",
        "category": "modern",
        "colors": ["#F6F9FC", "#E3F0F5", "#D0E7F0", "#AAC9DC", "#84A6B8"],
        "animation": "fade",
        "description": "Soft powder blues"
    },
    {
        "name": "Terracotta",
        "category": "modern",
        "colors": ["#E67E22", "#D35400", "#BA4A00", "#A04000", "#6E2C00"],
        "animation": "slide",
        "description": "Warm earthy oranges"
    },
    {
        "name": "Stone",
        "category": "modern",
        "colors": ["#BDBDBD", "#9E9E9E", "#7F7F7F", "#606060", "#414141"],
        "animation": "none",
        "description": "Natural stone grays"
    },
    {
        "name": "Canvas",
        "category": "modern",
        "colors": ["#F5E9DA", "#E5D5BB", "#D5C19C", "#B59B7A", "#957558"],
        "animation": "fade",
        "description": "Artistic canvas tones"
    },
    {
        "name": "Frost",
        "category": "modern",
        "colors": ["#E6F0FA", "#CCE1F5", "#B3D2F0", "#80B0D9", "#4D8EC2"],
        "animation": "glow",
        "description": "Frosty blues"
    },
    {
        "name": "Linen",
        "category": "modern",
        "colors": ["#FAF0E6", "#F5E5D5", "#F0DAC5", "#E0C9B0", "#D0B89A"],
        "animation": "none",
        "description": "Natural linen fabric"
    },
    {
        "name": "Seafoam",
        "category": "modern",
        "colors": ["#D4F1F9", "#B3E5F0", "#92D9E7", "#61C0BF", "#3BA0A0"],
        "animation": "fade",
        "description": "Fresh seafoam greens"
    },
    {
        "name": "Blush",
        "category": "modern",
        "colors": ["#FFE4E1", "#FFD1D1", "#FFB6C1", "#FF9EB5", "#FF7F9F"],
        "animation": "bounce",
        "description": "Soft pink blush tones"
    },
    {
        "name": "Pebble",
        "category": "modern",
        "colors": ["#D6D3D0", "#C2BFBC", "#AEABA8", "#8A8784", "#666360"],
        "animation": "none",
        "description": "Smooth pebble grays"
    },
    {
        "name": "Mist",
        "category": "modern",
        "colors": ["#E1E9F0", "#D2DDE8", "#C3D1E0", "#A4B8CF", "#859FBE"],
        "animation": "fade",
        "description": "Morning mist blues"
    },
    {
        "name": "Cappuccino",
        "category": "modern",
        "colors": ["#D7B19D", "#C49A85", "#B1836D", "#9E6C55", "#7B5544"],
        "animation": "slide",
        "description": "Coffee-inspired browns"
    },
    {
        "name": "Skyline",
        "category": "modern",
        "colors": ["#2C3E50", "#34495E", "#4A6B8A", "#6D8FB2", "#A9C9E8"],
        "animation": "fade",
        "description": "City skyline blues"
    },
    {
        "name": "Whisper",
        "category": "modern",
        "colors": ["#F5F0F6", "#EBE3F0", "#E1D6EA", "#CBB9DA", "#B59CCA"],
        "animation": "none",
        "description": "Whisper soft lavenders"
    },
    {
        "name": "Bamboo",
        "category": "modern",
        "colors": ["#D5E8D4", "#C1DFC4", "#ADD6B4", "#85C48C", "#5DB264"],
        "animation": "fade",
        "description": "Fresh bamboo greens"
    },
    {
        "name": "Pearl Gray",
        "category": "modern",
        "colors": ["#F0F0F0", "#E0E0E0", "#D0D0D0", "#B0B0B0", "#909090"],
        "animation": "none",
        "description": "Lustrous pearl grays"
    },
    {
        "name": "Porcelain",
        "category": "modern",
        "colors": ["#F4F6F7", "#E5E9EB", "#D6DCE0", "#B7C1C8", "#98A6B0"],
        "animation": "fade",
        "description": "Fine porcelain ceramics"
    },

    # ========== DARK (25) ==========
    {
        "name": "Night Sky",
        "category": "dark",
        "colors": ["#0A0F1E", "#141B2B", "#1E2738", "#3A4A6B", "#5A6E91"],
        "animation": "fade",
        "description": "Deep night blues"
    },
    {
        "name": "Carbon",
        "category": "dark",
        "colors": ["#1C1C1C", "#2D2D2D", "#3D3D3D", "#B0B0B0", "#E0E0E0"],
        "animation": "none",
        "description": "Carbon fiber blacks"
    },
    {
        "name": "Black Pearl",
        "category": "dark",
        "colors": ["#0B0E14", "#1A1F2A", "#293040", "#607D8B", "#B0BEC5"],
        "animation": "glow",
        "description": "Dark with metallic sheen"
    },
    {
        "name": "Vampire",
        "category": "dark",
        "colors": ["#1A0F0F", "#2D1A1A", "#402525", "#B22222", "#FFD700"],
        "animation": "fade",
        "description": "Dark reds with gold"
    },
    {
        "name": "Graphite Dark",
        "category": "dark",
        "colors": ["#1E1E1E", "#2E2E2E", "#3E3E3E", "#5E5E5E", "#7E7E7E"],
        "animation": "none",
        "description": "Deep graphite grays"
    },
    {
        "name": "Black Ice",
        "category": "dark",
        "colors": ["#0C0F15", "#181F27", "#242F39", "#4A90E2", "#7ED4E6"],
        "animation": "glow",
        "description": "Black with icy blue accents"
    },
    {
        "name": "Ebony",
        "category": "dark",
        "colors": ["#2A2825", "#3F3C37", "#545049", "#8B7D6B", "#C0B2A0"],
        "animation": "fade",
        "description": "Rich ebony wood"
    },
    {
        "name": "Dark Matter",
        "category": "dark",
        "colors": ["#0A0C0E", "#151A1F", "#202830", "#9B59B6", "#E74C3C"],
        "animation": "glow",
        "description": "Mysterious dark with purple and red"
    },
    {
        "name": "Raven",
        "category": "dark",
        "colors": ["#1A1A1D", "#2D2D32", "#404047", "#A0A0A8", "#D0D0D8"],
        "animation": "none",
        "description": "Raven black with gray accents"
    },
    {
        "name": "Black Gold",
        "category": "dark",
        "colors": ["#0F0F0F", "#1F1F1F", "#2F2F2F", "#CFB53B", "#E5C87B"],
        "animation": "slide",
        "description": "Black and gold luxury"
    },
    {
        "name": "Dark Forest",
        "category": "dark",
        "colors": ["#0F2417", "#1F3825", "#2F4C33", "#2E7D32", "#81C784"],
        "animation": "fade",
        "description": "Deep forest greens"
    },
    {
        "name": "Blacklight",
        "category": "dark",
        "colors": ["#121212", "#242424", "#363636", "#9C27B0", "#E1BEE7"],
        "animation": "glow",
        "description": "Black with UV purple"
    },
    {
        "name": "Dark Ocean",
        "category": "dark",
        "colors": ["#0A1922", "#122B34", "#1A3D46", "#2C7A7B", "#38B2AC"],
        "animation": "fade",
        "description": "Deep sea teals"
    },
    {
        "name": "Charred",
        "category": "dark",
        "colors": ["#1B1B1B", "#2F2F2F", "#434343", "#FF6F61", "#FF9A5A"],
        "animation": "bounce",
        "description": "Charcoal with warm accents"
    },
    {
        "name": "Dark Ruby",
        "category": "dark",
        "colors": ["#1A0F0F", "#2F1E1E", "#442D2D", "#B22222", "#E9967A"],
        "animation": "fade",
        "description": "Deep ruby reds"
    },
    {
        "name": "Abyss Blue",
        "category": "dark",
        "colors": ["#001220", "#002137", "#00304E", "#005A9C", "#4A9FD8"],
        "animation": "glow",
        "description": "Abyssal blues"
    },
    {
        "name": "Dark Iris",
        "category": "dark",
        "colors": ["#1A142B", "#2F2442", "#443459", "#7C4DFF", "#B388FF"],
        "animation": "fade",
        "description": "Dark purple iris"
    },
    {
        "name": "Coal",
        "category": "dark",
        "colors": ["#2A2A2A", "#3D3D3D", "#505050", "#888888", "#C0C0C0"],
        "animation": "none",
        "description": "Coal black and grays"
    },
    {
        "name": "Dark Turquoise",
        "category": "dark",
        "colors": ["#0A1F1F", "#143535", "#1E4B4B", "#40E0D0", "#9FE2E2"],
        "animation": "glow",
        "description": "Dark with turquoise pop"
    },
    {
        "name": "Midnight Sun",
        "category": "dark",
        "colors": ["#0D0D1A", "#1F1F33", "#32324D", "#FFD966", "#FFB347"],
        "animation": "fade",
        "description": "Dark blue with gold sun"
    },
    {
        "name": "Black Cherry",
        "category": "dark",
        "colors": ["#1A0F14", "#2F1E28", "#442D3C", "#B3446C", "#E6739F"],
        "animation": "slide",
        "description": "Dark with cherry red"
    },
    {
        "name": "Dark Sage",
        "category": "dark",
        "colors": ["#1A241A", "#2D392D", "#404E40", "#7A9E7A", "#A9C9A9"],
        "animation": "fade",
        "description": "Deep sage greens"
    },
    {
        "name": "Onyx Blue",
        "category": "dark",
        "colors": ["#0F1A2F", "#1F2F44", "#2F4459", "#4F7A9E", "#7F9FC9"],
        "animation": "none",
        "description": "Onyx black with blue undertones"
    },
    {
        "name": "Dark Amethyst",
        "category": "dark",
        "colors": ["#1A142A", "#2F2444", "#44345E", "#9B59B6", "#D7B0E8"],
        "animation": "glow",
        "description": "Deep amethyst purple"
    },
    {
        "name": "Black Sand",
        "category": "dark",
        "colors": ["#1C1C1C", "#2F2F2F", "#424242", "#C0A080", "#E0C0A0"],
        "animation": "fade",
        "description": "Black sand with beige accents"
    },

    # ========== NATURE (25) ==========
    {
        "name": "Rainforest",
        "category": "nature",
        "colors": ["#1A4D3E", "#2D6A4F", "#40916C", "#74C69D", "#B7E4C7"],
        "animation": "fade",
        "description": "Lush rainforest greens"
    },
    {
        "name": "Desert Dusk",
        "category": "nature",
        "colors": ["#C17B5E", "#B46A4B", "#A1593A", "#7F4A32", "#5E3B29"],
        "animation": "slide",
        "description": "Desert sunset tones"
    },
    {
        "name": "Mountain Lake",
        "category": "nature",
        "colors": ["#2E5A88", "#4A77A8", "#6694C8", "#A3C4E2", "#D0E2F0"],
        "animation": "fade",
        "description": "Mountain lake blues"
    },
    {
        "name": "Pine Forest",
        "category": "nature",
        "colors": ["#0B3B2F", "#1B4D3D", "#2B5F4B", "#3E7A5E", "#5F9E7A"],
        "animation": "none",
        "description": "Deep pine greens"
    },
    {
        "name": "Savanna",
        "category": "nature",
        "colors": ["#D9B382", "#C9A067", "#B98D4C", "#A07A3A", "#876728"],
        "animation": "fade",
        "description": "Savanna golds and browns"
    },
    {
        "name": "Coral Reef",
        "category": "nature",
        "colors": ["#FF7F50", "#FF6B5E", "#FF575C", "#FF8A7A", "#FFB6A3"],
        "animation": "bounce",
        "description": "Vibrant coral tones"
    },
    {
        "name": "Mossy Rock",
        "category": "nature",
        "colors": ["#6A6F5C", "#7E8470", "#929984", "#A9B29C", "#C0CBB4"],
        "animation": "fade",
        "description": "Moss-covered rocks"
    },
    {
        "name": "Autumn Leaves",
        "category": "nature",
        "colors": ["#D96C2B", "#C15B1C", "#A94A0D", "#8B4513", "#6B2E0A"],
        "animation": "slide",
        "description": "Falling autumn leaves"
    },
    {
        "name": "Tundra",
        "category": "nature",
        "colors": ["#A7B3A2", "#8C9A85", "#718168", "#56684B", "#3B4F2E"],
        "animation": "none",
        "description": "Cold tundra greens"
    },
    {
        "name": "Sunflower",
        "category": "nature",
        "colors": ["#FFD700", "#FFC800", "#FFB900", "#FAA51A", "#E69500"],
        "animation": "bounce",
        "description": "Bright sunflower yellows"
    },
    {
        "name": "Lavender Field",
        "category": "nature",
        "colors": ["#B2A4D4", "#9F8DC6", "#8C76B8", "#7A60AA", "#674A9C"],
        "animation": "fade",
        "description": "Rolling lavender fields"
    },
    {
        "name": "Cactus",
        "category": "nature",
        "colors": ["#3B5E3A", "#4C7749", "#5D9058", "#7EAE7A", "#9FCC9C"],
        "animation": "none",
        "description": "Desert cactus greens"
    },
    {
        "name": "Ocean Spray",
        "category": "nature",
        "colors": ["#2B7A7A", "#3F9494", "#53AEAE", "#7AC8C8", "#A1E2E2"],
        "animation": "fade",
        "description": "Ocean spray teals"
    },
    {
        "name": "Birch",
        "category": "nature",
        "colors": ["#E8E3D5", "#D8CFBC", "#C8BBA3", "#A89B84", "#887B65"],
        "animation": "none",
        "description": "Birch tree bark"
    },
    {
        "name": "Wildflower",
        "category": "nature",
        "colors": ["#E6A8D7", "#D68EC5", "#C674B3", "#B55AA1", "#A4408F"],
        "animation": "bounce",
        "description": "Wildflower purples"
    },
    {
        "name": "Seaside",
        "category": "nature",
        "colors": ["#B3D9D2", "#99C7C0", "#7FB5AE", "#65A39C", "#4B918A"],
        "animation": "fade",
        "description": "Coastal sea greens"
    },
    {
        "name": "Honey",
        "category": "nature",
        "colors": ["#F6C445", "#F4B43A", "#F2A42F", "#E0912A", "#CE7E25"],
        "animation": "glow",
        "description": "Golden honey tones"
    },
    {
        "name": "Mushroom Forest",
        "category": "nature",
        "colors": ["#B7A793", "#A6937F", "#957F6B", "#846B57", "#735743"],
        "animation": "fade",
        "description": "Forest floor mushrooms"
    },
    {
        "name": "Polar Ice",
        "category": "nature",
        "colors": ["#C0E0F0", "#B0D0E0", "#A0C0D0", "#80A0B0", "#608090"],
        "animation": "glow",
        "description": "Polar ice caps"
    },
    {
        "name": "Wheat Field",
        "category": "nature",
        "colors": ["#F5DEB3", "#F0D2A8", "#EBC69D", "#E0B886", "#D5AA6F"],
        "animation": "fade",
        "description": "Golden wheat fields"
    },
    {
        "name": "Jungle",
        "category": "nature",
        "colors": ["#1A4F3A", "#2D6E4F", "#408D64", "#63AC7E", "#86CB98"],
        "animation": "bounce",
        "description": "Lively jungle greens"
    },
    {
        "name": "Volcanic",
        "category": "nature",
        "colors": ["#4A3C31", "#5F4F41", "#746251", "#9A8874", "#C0AE97"],
        "animation": "fade",
        "description": "Volcanic rock and ash"
    },
    {
        "name": "Cherry Blossom",
        "category": "nature",
        "colors": ["#FFB7C5", "#FFA5B6", "#FF93A7", "#FF8198", "#FF6F89"],
        "animation": "bounce",
        "description": "Delicate cherry blossoms"
    },
    {
        "name": "Mangrove",
        "category": "nature",
        "colors": ["#1F4F3F", "#316A53", "#438567", "#60A184", "#7DBDA1"],
        "animation": "fade",
        "description": "Mangrove swamp greens"
    },
    {
        "name": "Sandstone",
        "category": "nature",
        "colors": ["#D5C3AA", "#C5B096", "#B59D82", "#A58A6E", "#95775A"],
        "animation": "none",
        "description": "Sandstone rock layers"
    },

    # ========== CORPORATE (25) ==========
    {
        "name": "Trust",
        "category": "corporate",
        "colors": ["#003B5C", "#005A8C", "#0079BC", "#4A9FD8", "#F5F5F5"],
        "animation": "none",
        "description": "Deep trustworthy blues"
    },
    {
        "name": "Steel",
        "category": "corporate",
        "colors": ["#4A5B6E", "#5F7085", "#74859C", "#A5B2C7", "#D6DFF2"],
        "animation": "fade",
        "description": "Industrial steel grays"
    },
    {
        "name": "Prestige",
        "category": "corporate",
        "colors": ["#1E2B3A", "#2E3F52", "#3E536A", "#657B93", "#8CA3BC"],
        "animation": "none",
        "description": "Prestigious dark blues"
    },
    {
        "name": "Professional",
        "category": "corporate",
        "colors": ["#2C3E50", "#34495E", "#4A6A7F", "#7F8C8D", "#BDC3C7"],
        "animation": "none",
        "description": "Standard professional palette"
    },
    {
        "name": "Integrity",
        "category": "corporate",
        "colors": ["#1A3A4A", "#2A4F63", "#3A647C", "#5A84A0", "#7AA4C4"],
        "animation": "fade",
        "description": "Honest blues and grays"
    },
    {
        "name": "Financial",
        "category": "corporate",
        "colors": ["#004B49", "#006B67", "#008B85", "#20B2AA", "#90EE90"],
        "animation": "slide",
        "description": "Stable green-teals"
    },
    {
        "name": "Corporate Gray",
        "category": "corporate",
        "colors": ["#4A4A4A", "#5F5F5F", "#747474", "#A0A0A0", "#CCCCCC"],
        "animation": "none",
        "description": "Neutral corporate grays"
    },
    {
        "name": "Enterprise Blue",
        "category": "corporate",
        "colors": ["#0F2A40", "#1F3F60", "#2F5480", "#4F74A0", "#6F94C0"],
        "animation": "fade",
        "description": "Enterprise-level blues"
    },
    {
        "name": "Analytics",
        "category": "corporate",
        "colors": ["#1A334F", "#2A4A6F", "#3A618F", "#5A81AF", "#7AA1CF"],
        "animation": "glow",
        "description": "Data-driven blues"
    },
    {
        "name": "Law & Order",
        "category": "corporate",
        "colors": ["#1E2B3A", "#2E3F52", "#3E536A", "#5D6B7A", "#7C838A"],
        "animation": "none",
        "description": "Serious, authoritative"
    },
    {
        "name": "Insurance Blue",
        "category": "corporate",
        "colors": ["#003366", "#1F4F8A", "#3F6BAE", "#6F97D2", "#9FC3F6"],
        "animation": "fade",
        "description": "Reliable insurance blues"
    },
    {
        "name": "Consulting Teal",
        "category": "corporate",
        "colors": ["#1A4F5A", "#2A6A7A", "#3A859A", "#5AA0B5", "#7ABBD0"],
        "animation": "slide",
        "description": "Teal for consulting firms"
    },
    {
        "name": "Banking",
        "category": "corporate",
        "colors": ["#1F3A3F", "#2F4F5A", "#3F6475", "#5F8495", "#7FA4B5"],
        "animation": "none",
        "description": "Conservative banking"
    },
    {
        "name": "Tech Corporate 2",
        "category": "corporate",
        "colors": ["#1A2A3A", "#2A3F55", "#3A5470", "#4A6990", "#5A7EB0"],
        "animation": "fade",
        "description": "Modern tech corporate"
    },
    {
        "name": "Medical",
        "category": "corporate",
        "colors": ["#1A5F7A", "#2A7A9A", "#3A95BA", "#5AB0D5", "#7ACBF0"],
        "animation": "none",
        "description": "Clean medical blues"
    },
    {
        "name": "Legal Navy",
        "category": "corporate",
        "colors": ["#13294B", "#1F3A63", "#2B4B7B", "#4B6C9B", "#6B8DBB"],
        "animation": "fade",
        "description": "Navy for legal firms"
    },
    {
        "name": "Accounting Green",
        "category": "corporate",
        "colors": ["#1A4F3F", "#2A6A52", "#3A8565", "#5AA07A", "#7ABB8F"],
        "animation": "none",
        "description": "Green for growth"
    },
    {
        "name": "Real Estate",
        "category": "corporate",
        "colors": ["#3A5F6F", "#4F7A8F", "#6495AF", "#89B0CF", "#AECBEF"],
        "animation": "slide",
        "description": "Stable real estate blues"
    },
    {
        "name": "Logistics",
        "category": "corporate",
        "colors": ["#FDB913", "#FAA51A", "#F78C1F", "#F47324", "#F15A29"],
        "animation": "bounce",
        "description": "Logistics orange/yellow"
    },
    {
        "name": "Energy",
        "category": "corporate",
        "colors": ["#1A6F5F", "#2A8A7A", "#3AA595", "#5ABBB0", "#7AD1CB"],
        "animation": "glow",
        "description": "Energy teals"
    },
    {
        "name": "Pharma",
        "category": "corporate",
        "colors": ["#4B3B5A", "#5F4B75", "#735B90", "#977BB5", "#BB9BDA"],
        "animation": "fade",
        "description": "Pharmaceutical purples"
    },
    {
        "name": "Aerospace",
        "category": "corporate",
        "colors": ["#1A2F4F", "#2A456F", "#3A5B8F", "#5A7BAF", "#7A9BCF"],
        "animation": "none",
        "description": "Aerospace blues"
    },
    {
        "name": "Defense",
        "category": "corporate",
        "colors": ["#2F3A3F", "#404F5A", "#516475", "#728595", "#93A6B5"],
        "animation": "none",
        "description": "Muted defense tones"
    },
    {
        "name": "Education",
        "category": "corporate",
        "colors": ["#1A4F6F", "#2A6A8F", "#3A85AF", "#5AA0CF", "#7ABBEF"],
        "animation": "fade",
        "description": "Educational blues"
    },
    {
        "name": "Nonprofit 2",
        "category": "corporate",
        "colors": ["#1A7A6F", "#2A958A", "#3AB0A5", "#5AC5BB", "#7ADAD1"],
        "animation": "fade",
        "description": "Warm teals for nonprofits"
    },

    # ========== PLAYFUL (25) ==========
    {
        "name": "Cotton Candy",
        "category": "playful",
        "colors": ["#FFB3D9", "#FF99CC", "#FF80BF", "#FF66B2", "#FF4DA6"],
        "animation": "bounce",
        "description": "Sweet cotton candy pinks"
    },
    {
        "name": "Gummy Bear",
        "category": "playful",
        "colors": ["#FF6B6B", "#FFD93D", "#6BCB77", "#4D96FF", "#9B59B6"],
        "animation": "glow",
        "description": "Gummy bear brights"
    },
    {
        "name": "Lemonade",
        "category": "playful",
        "colors": ["#FFF44F", "#FFE55C", "#FFD669", "#FFC776", "#FFB883"],
        "animation": "bounce",
        "description": "Refreshing lemonade yellows"
    },
    {
        "name": "Jellybean",
        "category": "playful",
        "colors": ["#FF5E5E", "#FF9F4D", "#FFE55C", "#6BCB77", "#5E9EFF"],
        "animation": "slide",
        "description": "Assorted jellybean colors"
    },
    {
        "name": "Fruit Punch",
        "category": "playful",
        "colors": ["#FF4F5E", "#FF6F4F", "#FF8F4F", "#FFAF4F", "#FFCF4F"],
        "animation": "glow",
        "description": "Fruit punch reds and oranges"
    },
    {
        "name": "Bubble Tea",
        "category": "playful",
        "colors": ["#F7CAC9", "#F4A2A2", "#F1878F", "#E86C7C", "#D95B6A"],
        "animation": "bounce",
        "description": "Bubble tea pinks"
    },
    {
        "name": "Macaron",
        "category": "playful",
        "colors": ["#FADADD", "#F9C7D1", "#F8B4C5", "#F7A1B9", "#F68EAD"],
        "animation": "fade",
        "description": "Delicate macaron pastels"
    },
    {
        "name": "Crayon Box",
        "category": "playful",
        "colors": ["#E40303", "#FF8C00", "#FFED00", "#008026", "#004DFF"],
        "animation": "bounce",
        "description": "Classic crayon primaries"
    },
    {
        "name": "Sprinkle",
        "category": "playful",
        "colors": ["#FFB3BA", "#FFDFBA", "#FFFFBA", "#BAFFC9", "#BAE1FF"],
        "animation": "glow",
        "description": "Rainbow sprinkle pastels"
    },
    {
        "name": "Cupcake",
        "category": "playful",
        "colors": ["#F9D5E5", "#F5B0CB", "#F28BB1", "#EF6697", "#EC417D"],
        "animation": "bounce",
        "description": "Frosting pinks"
    },
    {
        "name": "Lollipop 2",
        "category": "playful",
        "colors": ["#FFB347", "#FF9F4D", "#FF8B53", "#FF7759", "#FF635F"],
        "animation": "slide",
        "description": "Swirled lollipop oranges"
    },
    {
        "name": "Kite",
        "category": "playful",
        "colors": ["#6EC3E0", "#5AB3D1", "#46A3C2", "#3293B3", "#1E83A4"],
        "animation": "fade",
        "description": "Sky blues for kites"
    },
    {
        "name": "Party",
        "category": "playful",
        "colors": ["#FF69B4", "#FFA500", "#FFFF00", "#00FF00", "#0000FF"],
        "animation": "glow",
        "description": "Party brights"
    },
    {
        "name": "Pinata",
        "category": "playful",
        "colors": ["#FF4D4D", "#FF9F4D", "#FFF24D", "#7AC74F", "#4D9FFF"],
        "animation": "bounce",
        "description": "Pinata explosion"
    },
    {
        "name": "Bubblegum 2",
        "category": "playful",
        "colors": ["#FFC0CB", "#FFB3C6", "#FFA6C1", "#FF99BC", "#FF8CB7"],
        "animation": "slide",
        "description": "Classic bubblegum pinks"
    },
    {
        "name": "Jelly",
        "category": "playful",
        "colors": ["#D4A5F0", "#C58BEF", "#B671EE", "#A757ED", "#983DEC"],
        "animation": "glow",
        "description": "Jelly purple tones"
    },
    {
        "name": "Confetti 2",
        "category": "playful",
        "colors": ["#F94144", "#F9C74F", "#43AA8B", "#577590", "#F9844A"],
        "animation": "bounce",
        "description": "Confetti mix"
    },
    {
        "name": "Slime",
        "category": "playful",
        "colors": ["#A7E30E", "#96CE0B", "#85B908", "#74A405", "#638F02"],
        "animation": "glow",
        "description": "Gooey slime greens"
    },
    {
        "name": "Neon Pop",
        "category": "playful",
        "colors": ["#FF6EC7", "#FFD700", "#7CFC00", "#00FFFF", "#FF1493"],
        "animation": "glow",
        "description": "Neon party colors"
    },
    {
        "name": "Fizz",
        "category": "playful",
        "colors": ["#E0F2FE", "#BAE6FD", "#7DD3FC", "#38BDF8", "#0284C7"],
        "animation": "bounce",
        "description": "Fizzy soda blues"
    },
    {
        "name": "Carnival",
        "category": "playful",
        "colors": ["#FF3B3F", "#FF9F4D", "#FFE55C", "#6BCB77", "#5E9EFF"],
        "animation": "bounce",
        "description": "Carnival brights"
    },
    {
        "name": "Glitter",
        "category": "playful",
        "colors": ["#F4D3E9", "#E9B9D9", "#DE9FC9", "#D385B9", "#C86BA9"],
        "animation": "glow",
        "description": "Shimmery glitter pinks"
    },
    {
        "name": "Candy Cane",
        "category": "playful",
        "colors": ["#FF4D4D", "#FFFFFF", "#FF9F9F", "#E0E0E0", "#B22222"],
        "animation": "slide",
        "description": "Holiday candy stripes"
    },
    {
        "name": "Chalkboard",
        "category": "playful",
        "colors": ["#2F4F4F", "#4A6F6F", "#658F8F", "#C0D6D6", "#E0F2F2"],
        "animation": "fade",
        "description": "Chalkboard with colored chalk"
    },
    {
        "name": "Play Doh",
        "category": "playful",
        "colors": ["#FF4D4D", "#FF9F4D", "#FFF24D", "#4DFF4D", "#4D9FFF"],
        "animation": "bounce",
        "description": "Primary play-doh colors"
    },

    # ========== LUXURY (25) ==========
    {
        "name": "Gold Leaf",
        "category": "luxury",
        "colors": ["#4A3A2A", "#6B5A4A", "#8C7A6A", "#D4AF37", "#F0E68C"],
        "animation": "fade",
        "description": "Gold leaf on dark wood"
    },
    {
        "name": "Platinum 2",
        "category": "luxury",
        "colors": ["#E5E4E2", "#D1CFCD", "#BDBAB8", "#A5A2A0", "#8D8A88"],
        "animation": "none",
        "description": "Refined platinum metals"
    },
    {
        "name": "Caviar",
        "category": "luxury",
        "colors": ["#1A1A1A", "#2F2F2F", "#444444", "#C0A040", "#E0C080"],
        "animation": "fade",
        "description": "Black caviar with gold"
    },
    {
        "name": "Merlot",
        "category": "luxury",
        "colors": ["#4A1F2F", "#6B2F45", "#8C3F5B", "#C05A7A", "#F0A0C0"],
        "animation": "glow",
        "description": "Deep merlot wine"
    },
    {
        "name": "Champagne 2",
        "category": "luxury",
        "colors": ["#F7E7CE", "#F5D7B3", "#F2C8A0", "#E5B88B", "#D8A876"],
        "animation": "fade",
        "description": "Bubbly champagne"
    },
    {
        "name": "Truffle",
        "category": "luxury",
        "colors": ["#3A2A1F", "#553F30", "#705441", "#9B7A60", "#C6A07F"],
        "animation": "none",
        "description": "Dark chocolate truffle"
    },
    {
        "name": "Velvet 2",
        "category": "luxury",
        "colors": ["#2A1A3A", "#3F2A55", "#543A70", "#7A4F99", "#A064C2"],
        "animation": "glow",
        "description": "Plush velvet purples"
    },
    {
        "name": "Diamond",
        "category": "luxury",
        "colors": ["#F0F0F0", "#E0E0E0", "#D0D0D0", "#B0E0E0", "#90D0D0"],
        "animation": "glow",
        "description": "Diamond sparkle"
    },
    {
        "name": "Onyx 2",
        "category": "luxury",
        "colors": ["#1F1F1F", "#2F2F2F", "#3F3F3F", "#5F5F5F", "#7F7F7F"],
        "animation": "none",
        "description": "Polished onyx stone"
    },
    {
        "name": "Saffron",
        "category": "luxury",
        "colors": ["#4A3A1F", "#6B552F", "#8C703F", "#B5935F", "#DEB67F"],
        "animation": "fade",
        "description": "Precious saffron spice"
    },
    {
        "name": "Amethyst 2",
        "category": "luxury",
        "colors": ["#3A2A4A", "#553F6B", "#70548C", "#9B74B5", "#C694DE"],
        "animation": "glow",
        "description": "Deep amethyst gem"
    },
    {
        "name": "Pearl 2",
        "category": "luxury",
        "colors": ["#FDF5E6", "#FAF0DD", "#F5E6D3", "#E5D5C0", "#D5C4AD"],
        "animation": "fade",
        "description": "Lustrous pearls"
    },
    {
        "name": "Burgundy 2",
        "category": "luxury",
        "colors": ["#4A1F2A", "#6B2F40", "#8C3F56", "#B25F7C", "#D87FA2"],
        "animation": "none",
        "description": "Rich burgundy wine"
    },
    {
        "name": "Sapphire 2",
        "category": "luxury",
        "colors": ["#1F2A4A", "#2F406B", "#3F568C", "#5F7CB5", "#7FA2DE"],
        "animation": "glow",
        "description": "Deep blue sapphire"
    },
    {
        "name": "Emerald",
        "category": "luxury",
        "colors": ["#1F4A2A", "#2F6B40", "#3F8C56", "#5FB57C", "#7FDEA2"],
        "animation": "fade",
        "description": "Vibrant emerald green"
    },
    {
        "name": "Ruby 2",
        "category": "luxury",
        "colors": ["#4A1F1F", "#6B2F2F", "#8C3F3F", "#B55F5F", "#DE7F7F"],
        "animation": "glow",
        "description": "Deep ruby red"
    },
    {
        "name": "Jet Black",
        "category": "luxury",
        "colors": ["#0A0A0A", "#1F1F1F", "#343434", "#C0C0C0", "#FFFFFF"],
        "animation": "none",
        "description": "Jet black with silver"
    },
    {
        "name": "Rose Gold",
        "category": "luxury",
        "colors": ["#4A2A2A", "#6B4040", "#8C5656", "#B57A7A", "#DE9E9E"],
        "animation": "fade",
        "description": "Warm rose gold"
    },
    {
        "name": "Chocolate",
        "category": "luxury",
        "colors": ["#3A2A1A", "#553F2A", "#70543A", "#9B7A5A", "#C6A07A"],
        "animation": "none",
        "description": "Rich chocolate browns"
    },
    {
        "name": "Midnight Velvet",
        "category": "luxury",
        "colors": ["#1A1A2A", "#2F2F45", "#444460", "#6A6A9A", "#9090D4"],
        "animation": "glow",
        "description": "Midnight blue velvet"
    },
    {
        "name": "Cognac",
        "category": "luxury",
        "colors": ["#4A2F1A", "#6B452F", "#8C5B44", "#B57B64", "#DE9B84"],
        "animation": "fade",
        "description": "Aged cognac tones"
    },
    {
        "name": "Silver Screen",
        "category": "luxury",
        "colors": ["#2A2A2A", "#404040", "#565656", "#A0A0A0", "#EAEAEA"],
        "animation": "none",
        "description": "Hollywood silver"
    },
    {
        "name": "Truffle 2",
        "category": "luxury",
        "colors": ["#2A1F1A", "#40352F", "#564B44", "#7A6A60", "#9E897C"],
        "animation": "fade",
        "description": "Earthy truffle"
    },
    {
        "name": "Opal",
        "category": "luxury",
        "colors": ["#F0E5D0", "#E5D5C0", "#DAC5B0", "#C0B0A0", "#A69B90"],
        "animation": "glow",
        "description": "Opalescent shimmer"
    },
    {
        "name": "Majestic",
        "category": "luxury",
        "colors": ["#2A1F4A", "#40356B", "#564B8C", "#7A6AB5", "#9E89DE"],
        "animation": "fade",
        "description": "Majestic purple"
    },

    # ========== TECH (25) ==========
    {
        "name": "Silicon",
        "category": "tech",
        "colors": ["#1F2F3F", "#2F4055", "#3F516B", "#5F7195", "#7F91BF"],
        "animation": "none",
        "description": "Silicon chip grays"
    },
    {
        "name": "Binary",
        "category": "tech",
        "colors": ["#0F0F0F", "#1F1F1F", "#2F2F2F", "#00FF00", "#FFFFFF"],
        "animation": "glow",
        "description": "Classic green binary"
    },
    {
        "name": "Hacker",
        "category": "tech",
        "colors": ["#0C0C0C", "#1C1C1C", "#2C2C2C", "#33FF33", "#99FF99"],
        "animation": "glow",
        "description": "Hacker green on black"
    },
    {
        "name": "Cyber 2",
        "category": "tech",
        "colors": ["#0A0F1F", "#1A1F3F", "#2A2F5F", "#FF00FF", "#00FFFF"],
        "animation": "glow",
        "description": "Cyberpunk magenta/cyan"
    },
    {
        "name": "Digital",
        "category": "tech",
        "colors": ["#1A2A3A", "#2A4055", "#3A5670", "#4A6C95", "#5A82BA"],
        "animation": "fade",
        "description": "Digital blues"
    },
    {
        "name": "VR 2",
        "category": "tech",
        "colors": ["#1A0F2A", "#2F1F45", "#442F60", "#7A4FAA", "#B06FD4"],
        "animation": "glow",
        "description": "Virtual reality purples"
    },
    {
        "name": "AI 2",
        "category": "tech",
        "colors": ["#0F1F2A", "#1F3545", "#2F4B60", "#4F7AA0", "#6FA9E0"],
        "animation": "glow",
        "description": "Artificial intelligence blues"
    },
    {
        "name": "Cloud 9",
        "category": "tech",
        "colors": ["#1A2F4A", "#2A4A6F", "#3A6594", "#5A85B4", "#7AA5D4"],
        "animation": "fade",
        "description": "Cloud computing blues"
    },
    {
        "name": "Robot",
        "category": "tech",
        "colors": ["#2F3A4A", "#40556A", "#51708A", "#7190AA", "#91B0CA"],
        "animation": "none",
        "description": "Metallic robot grays"
    },
    {
        "name": "Circuit 2",
        "category": "tech",
        "colors": ["#1F2F1F", "#2F452F", "#3F5B3F", "#5F815F", "#7FA77F"],
        "animation": "fade",
        "description": "Circuit board greens"
    },
    {
        "name": "Data 2",
        "category": "tech",
        "colors": ["#1A2F3A", "#2F4F5F", "#446F84", "#6490A9", "#84B0CE"],
        "animation": "slide",
        "description": "Data stream blues"
    },
    {
        "name": "Neural",
        "category": "tech",
        "colors": ["#1F1F2F", "#35354A", "#4B4B65", "#7A7A9A", "#A9A9CF"],
        "animation": "glow",
        "description": "Neural network purples"
    },
    {
        "name": "Quantum 2",
        "category": "tech",
        "colors": ["#0F1A2F", "#1F2F4F", "#2F446F", "#4F649F", "#6F84CF"],
        "animation": "glow",
        "description": "Quantum mechanics blues"
    },
    {
        "name": "Byte 2",
        "category": "tech",
        "colors": ["#1F1F1F", "#2F2F2F", "#3F3F3F", "#00FF9F", "#00B8FF"],
        "animation": "bounce",
        "description": "Byte-sized neons"
    },
    {
        "name": "Terminal 2",
        "category": "tech",
        "colors": ["#0F0F0F", "#1F1F1F", "#2F2F2F", "#FFFF00", "#FFFFFF"],
        "animation": "fade",
        "description": "Yellow on black terminal"
    },
    {
        "name": "Hologram 2",
        "category": "tech",
        "colors": ["#1F2A3F", "#2F405F", "#3F567F", "#6F8FBF", "#9FB8FF"],
        "animation": "glow",
        "description": "Holographic blues"
    },
    {
        "name": "Cyberware",
        "category": "tech",
        "colors": ["#1A1F2A", "#2F3545", "#444B60", "#7A7A9A", "#AFAFD4"],
        "animation": "glow",
        "description": "Cyberpunk implants"
    },
    {
        "name": "Server",
        "category": "tech",
        "colors": ["#2A2A2A", "#404040", "#565656", "#808080", "#AAAAAA"],
        "animation": "none",
        "description": "Server room grays"
    },
    {
        "name": "Crypto 2",
        "category": "tech",
        "colors": ["#1F2F3F", "#2F4F5F", "#3F6F7F", "#F7931A", "#627EEA"],
        "animation": "slide",
        "description": "Crypto gold and blue"
    },
    {
        "name": "Fiber",
        "category": "tech",
        "colors": ["#1F2F2F", "#2F4F4F", "#3F6F6F", "#00FFFF", "#99FFFF"],
        "animation": "glow",
        "description": "Fiber optic cyan"
    },
    {
        "name": "AI Chip",
        "category": "tech",
        "colors": ["#1A1F2F", "#2F354F", "#444B6F", "#6F7F9F", "#9AAFC9"],
        "animation": "fade",
        "description": "AI chip blues"
    },
    {
        "name": "Neural Net",
        "category": "tech",
        "colors": ["#2A1F3F", "#40355F", "#564B7F", "#7A6AAF", "#9E89DF"],
        "animation": "glow",
        "description": "Neural network purples"
    },
    {
        "name": "Quantum Bit",
        "category": "tech",
        "colors": ["#0F1F2F", "#1F354F", "#2F4B6F", "#4F7A9F", "#6FA9CF"],
        "animation": "glow",
        "description": "Quantum computing"
    },
    {
        "name": "Silicon Valley",
        "category": "tech",
        "colors": ["#1A2F45", "#2F4A6A", "#44658F", "#6480AF", "#849BCF"],
        "animation": "fade",
        "description": "Startup blues"
    },
    {
        "name": "Edge",
        "category": "tech",
        "colors": ["#1F2F3A", "#2F4555", "#3F5B70", "#5F7B95", "#7F9BBA"],
        "animation": "slide",
        "description": "Edge computing grays"
    },

    # ========== MINIMAL (25) ==========
    {
        "name": "Pure White",
        "category": "minimal",
        "colors": ["#FFFFFF", "#FCFCFC", "#F8F8F8", "#F4F4F4", "#F0F0F0"],
        "animation": "none",
        "description": "Almost pure white"
    },
    {
        "name": "Gray Scale",
        "category": "minimal",
        "colors": ["#F0F0F0", "#D0D0D0", "#B0B0B0", "#909090", "#707070"],
        "animation": "none",
        "description": "Perfect gray gradient"
    },
    {
        "name": "Black & White",
        "category": "minimal",
        "colors": ["#000000", "#FFFFFF", "#F0F0F0", "#E0E0E0", "#C0C0C0"],
        "animation": "none",
        "description": "Strict black and white"
    },
    {
        "name": "Light Breeze",
        "category": "minimal",
        "colors": ["#F8FAFC", "#F0F4F8", "#E8EEF4", "#D0DCE8", "#B8CADC"],
        "animation": "fade",
        "description": "Subtle cool breeze"
    },
    {
        "name": "Warm Minimal",
        "category": "minimal",
        "colors": ["#FDF5E6", "#FAF0DD", "#F5E6D3", "#E5D5C0", "#D5C4AD"],
        "animation": "fade",
        "description": "Warm neutral minimal"
    },
    {
        "name": "Cool Minimal",
        "category": "minimal",
        "colors": ["#F0F8FF", "#E6F0FA", "#DCE8F5", "#C0D0E0", "#A4B8CC"],
        "animation": "none",
        "description": "Cool blue-whites"
    },
    {
        "name": "Slate Light",
        "category": "minimal",
        "colors": ["#F1F5F9", "#E2E8F0", "#CBD5E1", "#94A3B8", "#64748B"],
        "animation": "none",
        "description": "Light slate tones"
    },
    {
        "name": "Sand Minimal",
        "category": "minimal",
        "colors": ["#F5F0E6", "#EDE5D9", "#E5DACC", "#D5C9B8", "#C5B8A4"],
        "animation": "fade",
        "description": "Sandy minimalism"
    },
    {
        "name": "Pebble Minimal",
        "category": "minimal",
        "colors": ["#F0F0F0", "#E0E0E0", "#D0D0D0", "#C0C0C0", "#B0B0B0"],
        "animation": "none",
        "description": "Smooth pebble grays"
    },
    {
        "name": "Cloud White",
        "category": "minimal",
        "colors": ["#F9F9F9", "#F2F2F2", "#EBEBEB", "#DDDDDD", "#CFCFCF"],
        "animation": "fade",
        "description": "Fluffy cloud whites"
    },
    {
        "name": "Graphite Minimal",
        "category": "minimal",
        "colors": ["#E5E5E5", "#D5D5D5", "#C5C5C5", "#B5B5B5", "#A5A5A5"],
        "animation": "none",
        "description": "Graphite pencil grays"
    },
    {
        "name": "Off-White",
        "category": "minimal",
        "colors": ["#FEFEFA", "#FCFCF0", "#FAFAE6", "#F0F0D8", "#E6E6CA"],
        "animation": "none",
        "description": "Slight off-white"
    },
    {
        "name": "Taupe Minimal",
        "category": "minimal",
        "colors": ["#F5F0E6", "#EDE5D9", "#E5DACC", "#D5C9B8", "#C5B8A4"],
        "animation": "fade",
        "description": "Taupe neutrals"
    },
    {
        "name": "Gray Whisper",
        "category": "minimal",
        "colors": ["#F8F8F8", "#F0F0F0", "#E8E8E8", "#E0E0E0", "#D8D8D8"],
        "animation": "none",
        "description": "Whisper light grays"
    },
    {
        "name": "Stone Minimal",
        "category": "minimal",
        "colors": ["#F5F5F5", "#EEEEEE", "#E7E7E7", "#D9D9D9", "#CBCBCB"],
        "animation": "fade",
        "description": "Stone-like grays"
    },
    {
        "name": "Alabaster",
        "category": "minimal",
        "colors": ["#FAFAFA", "#F5F5F5", "#F0F0F0", "#E5E5E5", "#DADADA"],
        "animation": "none",
        "description": "Smooth alabaster"
    },
    {
        "name": "Cream",
        "category": "minimal",
        "colors": ["#FFFDD0", "#FFF8C9", "#FFF3C2", "#FFE8B0", "#FFDD9E"],
        "animation": "fade",
        "description": "Soft cream minimal"
    },
    {
        "name": "Snow White",
        "category": "minimal",
        "colors": ["#FFF9F9", "#FFF2F2", "#FFEBEB", "#FFDDDD", "#FFCFCF"],
        "animation": "none",
        "description": "Snowy whites"
    },
    {
        "name": "Limestone Light",
        "category": "minimal",
        "colors": ["#F5F0E6", "#EDE5D9", "#E5DACC", "#D5C9B8", "#C5B8A4"],
        "animation": "fade",
        "description": "Light limestone"
    },
    {
        "name": "Foggy",
        "category": "minimal",
        "colors": ["#F0F0F5", "#E5E5F0", "#DADAE6", "#C9C9D6", "#B8B8C6"],
        "animation": "none",
        "description": "Foggy blue-grays"
    },
    {
        "name": "Mist Minimal",
        "category": "minimal",
        "colors": ["#F0F5F5", "#E5F0F0", "#DAE6E6", "#C9D6D6", "#B8C6C6"],
        "animation": "fade",
        "description": "Misty aqua-grays"
    },
    {
        "name": "Pearl Light",
        "category": "minimal",
        "colors": ["#F8F8FF", "#F0F0FF", "#E8E8FF", "#D8D8F0", "#C8C8E0"],
        "animation": "glow",
        "description": "Pearlescent whites"
    },
    {
        "name": "Chalk White",
        "category": "minimal",
        "colors": ["#F9F9F9", "#F2F2F2", "#EBEBEB", "#DDDDDD", "#CFCFCF"],
        "animation": "none",
        "description": "Chalkboard background"
    },
    {
        "name": "Paper Light",
        "category": "minimal",
        "colors": ["#FCFCFC", "#F7F7F7", "#F2F2F2", "#E7E7E7", "#DCDCDC"],
        "animation": "fade",
        "description": "Recycled paper"
    },
    {
        "name": "Bone",
        "category": "minimal",
        "colors": ["#FDF5E6", "#F5E6D3", "#EDD7C0", "#DDC8B0", "#CDB9A0"],
        "animation": "none",
        "description": "Bone white minimal"
    }
]

def select_themes_for_prompt(prompt: str, max_themes: int = 5) -> list:
    """
    Given a user prompt, return a list of up to `max_themes` theme dictionaries
    that are relevant to the prompt. Includes exact matches and category matches.
    """
    prompt_lower = prompt.lower()
    
    # First, check if any theme name is mentioned exactly
    mentioned_themes = []
    for theme in THEMES:
        if theme["name"].lower() in prompt_lower:
            mentioned_themes.append(theme)
    
    if mentioned_themes:
        # If user explicitly named themes, prioritize them and fill with similar category
        categories = {t["category"] for t in mentioned_themes}
        similar = []
        for cat in categories:
            similar.extend([t for t in THEMES if t["category"] == cat and t not in mentioned_themes])
        # Shuffle and limit
        random.shuffle(similar)
        result = mentioned_themes + similar[:max_themes - len(mentioned_themes)]
        return result[:max_themes]
    
    # Otherwise, score by keyword matches
    scores = {}
    for theme in THEMES:
        score = 0
        # Check category keywords
        cat_keywords = THEME_CATEGORIES[theme["category"]]["keywords"]
        for kw in cat_keywords:
            if kw in prompt_lower:
                score += 1
        # Check theme description
        for word in theme["description"].lower().split():
            if word in prompt_lower:
                score += 0.5
        scores[theme["name"]] = score
    
    # Sort by score descending
    sorted_themes = sorted(THEMES, key=lambda t: scores[t["name"]], reverse=True)
    # Return top themes, but ensure at least a few
    top = [t for t in sorted_themes if scores[t["name"]] > 0][:max_themes]
    if len(top) < max_themes:
        # Fill with random themes from diverse categories
        needed = max_themes - len(top)
        existing_cats = {t["category"] for t in top}
        candidates = [t for t in THEMES if t["category"] not in existing_cats and t not in top]
        random.shuffle(candidates)
        top.extend(candidates[:needed])
    return top[:max_themes]

def format_themes_for_prompt(themes: list) -> str:
    """Format theme list as a readable block for the AI prompt."""
    lines = ["Here are some suggested color palettes and animation styles you could use:"]
    for i, t in enumerate(themes, 1):
        colors = ', '.join(t['colors'])
        lines.append(f"{i}. {t['name']} ({t['category']}): colors {colors}, animation: {t['animation']} — {t['description']}")
    lines.append("\nYou can pick one theme or blend elements from multiple. Ensure the final design is cohesive and professional.")
    return "\n".join(lines)