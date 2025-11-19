#!/usr/bin/env python3
"""Test script to validate album lookup improvements."""

from album_lookup import guess_album

# Test cases from implementation plan
test_cases = [
    # Classic Rock - Heavy Remaster Activity
    ("The Beatles", "Come Together", "Abbey Road", "many_remasters"),
    ("The Beatles", "Let It Be", "Let It Be", "many_remasters"),
    ("The Beatles", "Yesterday", "Help!", "compilation_appearance"),
    ("Pink Floyd", "Wish You Were Here", "Wish You Were Here", "deluxe_editions"),
    ("Pink Floyd", "Another Brick in the Wall (Part 2)", "The Wall", "deluxe_editions"),
    ("Led Zeppelin", "Stairway to Heaven", "Led Zeppelin IV", "untitled_album"),
    ("Led Zeppelin", "Whole Lotta Love", "Led Zeppelin II", "many_remasters"),
    ("Fleetwood Mac", "Go Your Own Way", "Rumours", "multiple_compilations"),
    ("Fleetwood Mac", "Dreams", "Rumours", "recent_viral_popularity"),
    ("Queen", "Bohemian Rhapsody", "A Night at the Opera", "many_remasters"),
    ("Queen", "We Will Rock You", "News of the World", "compilation_heavy"),
    ("The Rolling Stones", "Paint It Black", "Aftermath", "many_remasters"),
    ("The Rolling Stones", "Sympathy for the Devil", "Beggars Banquet", "many_remasters"),
    ("David Bowie", "Heroes", "Heroes", "many_remasters"),
    ("David Bowie", "Space Oddity", "Space Oddity", "many_remasters"),
    
    # 70s-80s Albums with Anniversary Editions
    ("Stevie Wonder", "Superstition", "Talking Book", "many_remasters"),
    ("Eagles", "Hotel California", "Hotel California", "deluxe_editions"),
    ("Bruce Springsteen", "Born to Run", "Born to Run", "anniversary_editions"),
    ("Michael Jackson", "Billie Jean", "Thriller", "many_remasters"),
    ("Michael Jackson", "Beat It", "Thriller", "deluxe_editions"),
    ("Prince", "Purple Rain", "Purple Rain", "deluxe_editions"),
    ("Madonna", "Like a Virgin", "Like a Virgin", "many_remasters"),
    ("U2", "With or Without You", "The Joshua Tree", "anniversary_editions"),
    ("Guns N' Roses", "Sweet Child O' Mine", "Appetite for Destruction", "deluxe_editions"),
    ("Metallica", "Enter Sandman", "Metallica", "many_remasters"),
    
    # 90s Albums with Recent Remasters
    ("Nirvana", "Smells Like Teen Spirit", "Nevermind", "anniversary_editions"),
    ("Pearl Jam", "Alive", "Ten", "deluxe_editions"),
    ("Radiohead", "Creep", "Pablo Honey", "many_remasters"),
    ("Radiohead", "Karma Police", "OK Computer", "deluxe_editions"),
    ("Oasis", "Wonderwall", "What's the Story Morning Glory", "deluxe_editions"),
    ("The Smashing Pumpkins", "1979", "Mellon Collie and the Infinite Sadness", "deluxe_editions"),
    ("Red Hot Chili Peppers", "Under the Bridge", "Blood Sugar Sex Magik", "many_remasters"),
    ("Green Day", "Basket Case", "Dookie", "many_remasters"),
    
    # 2000s+ Popular Albums
    ("Coldplay", "Yellow", "Parachutes", "many_remasters"),
    ("The Killers", "Mr. Brightside", "Hot Fuss", "many_remasters"),
    ("Arctic Monkeys", "Do I Wanna Know?", "AM", "recent_deluxe"),
    ("Amy Winehouse", "Rehab", "Back to Black", "deluxe_editions"),
    ("Adele", "Rolling in the Deep", "21", "deluxe_editions"),
    
    # Jazz/Soul Classics - Heavy Reissue Activity
    ("Miles Davis", "So What", "Kind of Blue", "many_remasters"),
    ("John Coltrane", "Giant Steps", "Giant Steps", "many_remasters"),
    ("Marvin Gaye", "What's Going On", "What's Going On", "deluxe_editions"),
    ("Bill Withers", "Ain't No Sunshine", "Just As I Am", "compilation_heavy"),
    
    # Edge Cases
    ("Toto", "Africa", "Toto IV", "many_remasters"),
    ("A-ha", "Take On Me", "Hunting High and Low", "deluxe_editions"),
    ("Tears for Fears", "Everybody Wants to Rule the World", "Songs from the Big Chair", "deluxe_editions"),
    ("Daft Punk", "Get Lucky", "Random Access Memories", "deluxe_editions"),
    
    # Albums with Confusing Names
    ("Weezer", "Say It Ain't So", "Weezer", "self_titled"),
    ("Metallica", "Nothing Else Matters", "Metallica", "self_titled"),
    ("Peter Gabriel", "Sledgehammer", "So", "simple_title"),
    
    # Very Old Albums (50+ years)
    ("Bob Dylan", "Like a Rolling Stone", "Highway 61 Revisited", "many_remasters"),
    ("Simon & Garfunkel", "The Sound of Silence", "Wednesday Morning, 3 A.M.", "electric_version_confusion"),
    ("The Doors", "Light My Fire", "The Doors", "many_remasters"),
    ("Jimi Hendrix", "Purple Haze", "Are You Experienced", "many_remasters"),
]


def normalize_album_name(name):
    """Normalize album name for comparison."""
    if not name:
        return ""
    # Remove common variations
    normalized = name.lower().strip()
    # Remove "the" prefix
    if normalized.startswith("the "):
        normalized = normalized[4:]
    return normalized


def test_album_lookup():
    """Run all test cases and report results."""
    total = len(test_cases)
    correct = 0
    failed_cases = []
    
    print(f"Running {total} test cases...\n")
    print("=" * 80)
    
    for artist, title, expected_album, challenge in test_cases:
        result = guess_album(artist, title)
        
        if result:
            album_name = result.album
            confidence = result.confidence
            source = result.source
            album_type = result.album_type
            release_date = result.release_date.year if result.release_date else "Unknown"
            raw_album = result.raw_album or album_name
            
            # Normalize for comparison
            normalized_result = normalize_album_name(album_name)
            normalized_expected = normalize_album_name(expected_album)
            
            is_correct = normalized_result == normalized_expected
            status = "✓" if is_correct else "✗"
            
            if is_correct:
                correct += 1
            else:
                failed_cases.append({
                    "artist": artist,
                    "title": title,
                    "expected": expected_album,
                    "got": album_name,
                    "raw": raw_album,
                    "confidence": confidence,
                    "year": release_date,
                    "challenge": challenge,
                })
            
            print(f"{status} {artist} - {title}")
            print(f"   Expected: {expected_album}")
            print(f"   Got:      {album_name} (raw: {raw_album})")
            print(f"   Conf: {confidence:.2f} | Year: {release_date} | Type: {album_type} | Source: {source}")
            print(f"   Challenge: {challenge}")
        else:
            print(f"✗ {artist} - {title}")
            print(f"   Expected: {expected_album}")
            print(f"   Got:      NO RESULT")
            failed_cases.append({
                "artist": artist,
                "title": title,
                "expected": expected_album,
                "got": "NO RESULT",
                "raw": "",
                "confidence": 0.0,
                "year": "N/A",
                "challenge": challenge,
            })
        
        print("-" * 80)
    
    # Summary
    accuracy = (correct / total) * 100
    print("\n" + "=" * 80)
    print(f"RESULTS: {correct}/{total} correct ({accuracy:.1f}% accuracy)")
    print("=" * 80)
    
    if failed_cases:
        print(f"\nFAILED CASES ({len(failed_cases)}):")
        print("=" * 80)
        for case in failed_cases:
            print(f"\n{case['artist']} - {case['title']}")
            print(f"  Expected: {case['expected']}")
            print(f"  Got:      {case['got']} (raw: {case['raw']})")
            print(f"  Confidence: {case['confidence']:.2f} | Year: {case['year']}")
            print(f"  Challenge: {case['challenge']}")
    
    return accuracy, failed_cases


if __name__ == "__main__":
    accuracy, failed = test_album_lookup()
