import random
import argparse
import sys

# --- Word Lists for Realistic Paths ---
# You can expand these lists with more words relevant to your demo application
NOUNS = [
    "article", "product", "blog", "user", "category", "item", "service",
    "document", "guide", "tutorial", "news", "event", "gallery",
    "portfolio", "team", "contact", "about", "faq", "help", "support",
    "download", "resource", "case-study", "whitepaper", "report",
    "press-release", "feature", "integration", "partner", "testimonial",
    "review", "pricing", "plan", "offer", "promotion", "coupon", "api",
    "developer", "sdk", "reference", "changelog", "release-notes",
    "webinar", "conference", "workshop", "meetup", "career", "job",
    "opening", "internship", "newsletter", "subscription", "membership",
    "account", "profile", "dashboard", "settings", "preferences",
    "forum", "community", "discussion", "thread", "comment", "ebook",
    "brochure", "datasheet", "specification", "manual", "announcement",
    "update", "alert", "notification", "demo", "example", "sample",
    "template", "snippet"
]

ADJECTIVES = [
    "new", "featured", "popular", "latest", "archived", "updated", "important",
    "technical", "creative", "business", "marketing", "sales", "engineering"
]

ACTIONS = [
    "view", "edit", "create", "list", "details", "overview", "summary", "index"
]

# Combine all word lists for more variety
ALL_WORDS = list(set(NOUNS + ADJECTIVES + ACTIONS))

def generate_path_segment():
    """Generates a single, random segment for a URL path."""
    return random.choice(ALL_WORDS)

def generate_path(max_depth):
    """
    Generates a random URL path with a variable number of segments.

    Args:
        max_depth (int): The maximum number of segments the path can have.

    Returns:
        str: A randomly generated URL path string (e.g., "/product/new/details").
    """
    depth = random.randint(1, max_depth)
    segments = [generate_path_segment() for _ in range(depth)]
    return "/" + "/".join(segments)

def find_ultimate_dest(path, final_dest_map):
    """
    Finds the final destination of a redirect chain and applies path compression.
    This corrected version prevents infinite loops and performs efficiently.

    Args:
        path (str): The starting path.
        final_dest_map (dict): The cache of known next-hops in redirect chains.

    Returns:
        str: The ultimate destination of the path.
    """
    # 1. Find the root/ultimate destination by following the chain,
    #    and keep track of the path taken.
    root = path
    path_to_root = []
    while root in final_dest_map:
        path_to_root.append(root)
        root = final_dest_map[root]

    # 2. Path Compression: Update all nodes in the traversed path to point
    #    directly to the root. This flattens the chain for future lookups.
    for node in path_to_root:
        final_dest_map[node] = root

    return root

def generate_redirect_rules(num_rules, max_depth, common_prefix_probability):
    """
    Generates a set of unique redirect rules efficiently, preventing loops.

    Args:
        num_rules (int): The number of redirect rules to generate.
        max_depth (int): The maximum number of segments for generated paths.
        common_prefix_probability (float): The chance (0.0 to 1.0) of a destination sharing a prefix.

    Returns:
        set: A set of strings, where each string is a redirect rule.
    """
    rules = set()
    source_paths = set()
    final_dest_map = {}

    rules_generated = 0

    # --- Guardrail for Path Exhaustion ---
    # Estimate the number of possible unique paths. This is a simplification.
    # A more accurate calculation is complex, but this gives a reasonable order of magnitude.
    num_possible_paths = sum([len(ALL_WORDS) ** i for i in range(1, max_depth + 1)])
    if num_rules > num_possible_paths * 0.9: # If we're close to exhaustion
        sys.stderr.write(f"Warning: Requested rules ({num_rules}) is high relative to possible unique paths (~{num_possible_paths}).\n")
        sys.stderr.write("Generation may be slow or fail if it cannot find unique source paths.\n")

    while rules_generated < num_rules:
        # 1. Generate a unique source path.
        source_path = generate_path(max_depth)

        # Heuristic to prevent getting stuck if path namespace is exhausted
        generation_attempts = 0
        while source_path in source_paths:
            source_path = generate_path(max_depth)
            generation_attempts += 1
            if generation_attempts > num_rules * 2 and num_rules > 1000:
                sys.stderr.write(f"\nError: Could not find a unique source path after {generation_attempts} attempts.\n")
                sys.stderr.write(f"You may have exhausted the path namespace. Generated {rules_generated} rules.\n")
                return rules


        # 2. Find a valid destination path that doesn't create a loop.
        for _ in range(50):
            destination_path = ""

            if random.random() < common_prefix_probability and source_path.count('/') > 1:
                segments = source_path.strip('/').split('/')
                prefix_end_index = random.randint(1, len(segments) - 1)
                prefix = segments[:prefix_end_index]

                new_segments_count = 0
                if len(prefix) < max_depth:
                    new_segments_count = random.randint(1, max_depth - len(prefix))
                new_segments = [generate_path_segment() for _ in range(new_segments_count)]
                destination_path = "/" + "/".join(prefix + new_segments)
            else:
                destination_path = generate_path(max_depth)

            # 3. Validate the potential rule using the optimized find function.
            ultimate_dest = find_ultimate_dest(destination_path, final_dest_map)

            if source_path != destination_path and ultimate_dest != source_path:
                rules.add(f"{source_path} {destination_path}")
                source_paths.add(source_path)

                # Update the map for the new source to point to its next hop.
                final_dest_map[source_path] = destination_path

                rules_generated += 1

                if rules_generated % 100000 == 0 and rules_generated > 0:
                    sys.stderr.write(f"Generated {rules_generated}/{num_rules} rules...\n")
                    sys.stderr.flush()

                break

    return rules

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate a list of realistic static redirect rules.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("-n", "--num-rules", type=int, default=1000, help="The total number of redirect rules to generate (default: 1000).")
    parser.add_argument("-d", "--max-depth", type=int, default=4, help="The maximum number of segments in a URL path (default: 4).")
    parser.add_argument("-p", "--prefix-prob", type=float, default=0.7, help="Probability (0.0 to 1.0) that a destination path will share a prefix with the source (default: 0.7).")

    args = parser.parse_args()

    if not 0.0 <= args.prefix_prob <= 1.0:
        parser.error("Probability must be a float between 0.0 and 1.0.")

    sys.stderr.write(f"Generating {args.num_rules} redirect rules...\n")
    redirect_rules = generate_redirect_rules(args.num_rules, args.max_depth, args.prefix_prob)

    sys.stderr.write("Generation complete. Printing rules...\n")
    for rule in sorted(list(redirect_rules)):
        print(rule)
