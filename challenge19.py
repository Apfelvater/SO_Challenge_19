from itertools import batched, permutations
import json
import logging
import math
import os
import urllib.request
import sys
import threading
import time

MAX_WORD_SIZE = 16

CHECK_WITH_API = True
MAX_API_REQUESTS = 1000

merriam_webster_api_url = "https://dictionaryapi.com/api/v3/references/thesaurus/json/{word}?key={api_key}"
wordlist_url = "https://raw.githubusercontent.com/dwyl/english-words/refs/heads/master/words_alpha.txt"
wordlist_file = "./words_alpha.txt"

one_letter_words = {"a", "i"}
two_letter_words = {"am","an","as","at","ah","aw","ax","ay",
                    "be","by","bo","ba","bi",
                    "do","de",
                    "eh","em","en","er","ex",
                    "fa","fe",
                    "go","gi",
                    "ha","he","hi","ho",
                    "if","in","is","it","id",
                    "jo",
                    "ka","ki",
                    "la","lo","li",
                    "me","my","ma","mo","mu",
                    "no","ne","na","nu",
                    "oh","oi","om","on","or","ow","ox","oy",
                    "pa","pe","pi","po",
                    "qi",
                    "re",
                    "so","sh","si",
                    "to","ta","ti",
                    "uh","um","up","us",
                    "we","wo",
                    "ye","yo","ya",
                    "za"}

class Wordlists:
    # Algorithm choices:
    BY_EXISTING = 0
    PERMUTATIONS = 1
    THREADED_PERMUTATIONS = 2 #NOT (fully) IMPLEMENTED
    THREADED_BY_EXISTING = 3 #NOTIMPLEMENTED

    def __init__(self, max_word_size, dictionary_api_url, api_key, logger, wordlist = []):
        self._wordsets = wordlist
        self.origin = None
        self._max_word_size = max_word_size
        self._api_url = dictionary_api_url
        self._api_key = api_key
        self.word_meta_data = {}
        self.n_api_requests = 0
        self.logger:logging.Logger = logger
        self._lock = threading.Lock()
        self.find_word = self.find_word_by_existing_words

    def set_algorithm(self, algorithm : int):
        if algorithm == self.BY_EXISTING:
            self.find_word = self.find_word_by_existing_words
        elif algorithm == self.PERMUTATIONS:
            self.find_word = self.find_word_by_permutations
        elif algorithm == self.THREADED_PERMUTATIONS:
            raise NotImplementedError
            self.find_word = self.find_word_by_permutations_parallel
        elif algorithm == self.THREADED_BY_EXISTING:
            raise NotImplementedError

    def read_wordlist_from_file(self, filepath):
        self._wordsets = [set() for i in range(self._max_word_size + 1)]
        with open(filepath, "rb") as f_in:
            for w in f_in:
                try:
                    w = w.decode("utf-8").strip("\n\r\t ")
                except:
                    self.logger.warning(f"Can't decode '{w}'. Skipping...")
                if w.isalpha() and len(w) <= self._max_word_size:
                    self._wordsets[len(w) - 1].add(w.lower())
        self._wordsets[0] = one_letter_words
        self._wordsets[1] = two_letter_words

    def init_wordlist(self, filepath, alt_url = ""):
        if os.path.exists(filepath):
            self.logger.info("Reading wordlist from file ...")
            self.origin = filepath
            self.read_wordlist_from_file(filepath)
        else:
            if not alt_url:
                return 
            self.origin = alt_url
            self.logger.info("Trying to download wordlist from", alt_url, "...")
            with urllib.request.urlopen(alt_url) as response:
                self.logger.info(f"Saving wordlists to '{filepath}'...")
                with open(filepath, "wb") as writefile:
                    writefile.write(response.read())

            # Now read the file (i know its write and instant read, but who cares)
            self.init_wordlist(filepath)

    def exists_in_wordlist(self, word):
        return word in self._wordsets[len(word) - 1]
    
    def check_word_is_source(source_wordlist, word):
        if len(word) <= 3:
            return False
        else:
            return word in source_wordlist

    # "BY_EXISTING"
    def find_word_by_existing_words(self, size, letters, source_words, exclude):
        if len(letters) < size: return None

        def word_can_be_build(word, chars):
            """Can the word 'word' be built with characters in 'chars'?"""
            for char in word:
                if word.count(char) > chars.count(char):
                    return False
            return True

        self.logger.debug(f"Looking through all existing words of size {size} and checking if they can be built with remaining letters...")
        for existing_word in self._wordsets[size-1]:
            if existing_word not in exclude and not Wordlists.check_word_is_source(source_words, existing_word):
                if word_can_be_build(existing_word, letters) is True:
                    if CHECK_WITH_API is False or self.exists_in_mwd(existing_word):
                        return existing_word
                
        return None

    # "PERMUTATIONS"
    def find_word_by_permutations(self, size, letters, source_words, exclude):

        if len(letters) < size: return None

        # We have to iterate through all combinations of letters in letters of size size
        n_permutations = int(math.factorial(len(letters)) / math.factorial(len(letters)-size))
        letter_permutations = permutations(letters, size)
        self.logger.debug(f"Trying out {n_permutations} different permutations of letters '{letters}'...")
        for word_candidate in letter_permutations:
            word_candidate = "".join(word_candidate)
            if word_candidate not in exclude and not Wordlists.check_word_is_source(source_words, word_candidate):
                # Hardcoded, short words:
                if len(word_candidate) <= 2:
                    if word_candidate in one_letter_words or word_candidate in two_letter_words:
                        return word_candidate
                elif self.exists_in_wordlist(word_candidate) is True:
                    # If we want to proof-check the word with API, return only if it exists in merriam-webster
                    if CHECK_WITH_API is False or self.exists_in_mwd(word_candidate):
                        return word_candidate
        return None
    
    # "THREADED_PERMUTATIONS"
    def find_word_by_permutations_parallel(self, size, letters, source_words, exclude):

        if len(letters) < size: return None

        # We have to iterate through all combinations of letters in letters of size size
        n_permutations = int(math.factorial(len(letters)) / math.factorial(len(letters)-size))
        letter_permutations = permutations(letters, size)
        self.logger.debug(f"Trying out {n_permutations} different permutations of letters '{letters}'...")
        if n_permutations >= 250000000:
            max_threads = os.cpu_count()
            self.logger.debug(f"Splitting workload over {max_threads} threads.")
            threads = []
            results = []
            for chunk in batched(letter_permutations, n_permutations/max_threads):
                t = threading.Thread(target=self.split_find_word, args= (chunk, exclude, source_words))
                threads.append(t)
                t.start()
            # TODO: Either: If one thread finds a word, interrupt the other threads.
            #       Or (better): save results from ALL threads (all should be ca. equally fast), for later usage
            # TODO: Result value!!
            for t in threads:
                t.join()
        return None
    # "THREADED_PERMUTATIONS"
    def split_find_word(self, chunk, exclude, source_words):
        
        for word_candidate in chunk:
            word_candidate = "".join(word_candidate)
            if word_candidate not in exclude and not Wordlists.check_word_is_source(source_words, word_candidate):
                # Hardcoded, short words:
                if len(word_candidate) <= 2:
                    if word_candidate in one_letter_words or word_candidate in two_letter_words:
                        return word_candidate
                elif self.exists_in_wordlist(word_candidate) is True:
                    # If we want to proof-check the word with API, return only if it exists in merriam-webster
                    if CHECK_WITH_API is False or self.exists_in_mwd(word_candidate):
                        return word_candidate

    # API functions

    def exists_in_mwd(self, word):
        if word not in self.word_meta_data:
            with self._lock:
                self.get_meta(word)
        if len(self.word_meta_data[word]) == 0:
            return False
        else:
            return True

    def get_meta(self, word):
        if word not in self.word_meta_data:
            self.logger.debug(f"Making API request for word '{word}'...")
            query_URI = self._api_url.format(word=word, api_key=self._api_key)
            if self.n_api_requests > MAX_API_REQUESTS:
                self.logger.warning(f"Made more than {MAX_API_REQUESTS} requests. Accepting word without checkup.")
                self.word_meta_data[word] = [["PLACEBO"]]
                return -1
            self.n_api_requests += 1
            dictionary_entry = []
            with urllib.request.urlopen(query_URI) as json_response:
                try:
                    dictionary_entry = json.loads(json_response.read())
                except:
                    self.logger.error(f"Reading response as json from query '{query_URI}' failed!")
                    raise
            self.word_meta_data[word] = []
            for entry in filter(lambda x: type(x) == dict, dictionary_entry):
                if "meta" in entry:
                    if entry["meta"] not in self.word_meta_data[word]:
                        self.word_meta_data[word].append(entry["meta"])
    
    def is_offensive(self, word):
        # Returns False if word does not exist
        #   -> Apparently offensive words contain no meta data
        #   -> Allways check if word exists
        if word not in self.word_meta_data:
            with self._lock:
                self.get_meta(word)
        for meta_entry in self.word_meta_data[word]:
            if meta_entry["offensive"] is True:
                return True
        return False

def get_sanitized_sentence(s):
    s2 = ""
    for c in s:
        if ord(c) in range(65,90)\
            or ord(c) in range(97,122)\
            or c == " ":
            s2 += c
    return s2

def get_sanitized_lowercase_sentence(s):
    return get_sanitized_sentence(s).lower()

def contains_effective_vowel(word):
    for vowel in "aeiouyw":
        if vowel in word:
            return True
    return False

def build_new_sentence(orig_sentence, wordlists_searcher, logger, wordsize_iterator):
    orig_letters = get_sanitized_lowercase_sentence(orig_sentence)
    orig_words = orig_letters.split(" ")
    logger.info(f"Available letters: {orig_letters}")
    new_words = []
    new_word_sizes = []
    exclude_word_lists = [[]]
    new_word_sizes.append(wordsize_iterator(1,17))
    word_size = next(new_word_sizes[-1])    # This shouldnt throw StopIteration
    remaining_original_letters = orig_letters[:].replace(" ", "")

    # As long as we do not have used all letters, try...
    while len(remaining_original_letters) > 0:
        current_word = None
        # ... try to find next word
        try:
            # while we have not found a next word...
            while current_word is None:
                # if we cant build a word, raise StopIteration
                if not contains_effective_vowel(remaining_original_letters):
                    raise StopIteration(f"No effective vowel in '{remaining_original_letters}'!")

                # ... and find a word of that length in the remaining letters
                current_word = wordlists_searcher.find_word(word_size, remaining_original_letters, source_words= orig_words, exclude= exclude_word_lists[-1])
                # ... change the word size
                if current_word is None:
                    info_str = f"No word of length {word_size} found. "
                    word_size = next(new_word_sizes[-1])
                    info_str += f"Trying with length {word_size}..."
                    logger.debug(info_str)

            # then save the current word
            info_str = f"Found word: '{current_word}' "
            new_words.append(current_word)
            info_str += f"Current new words are '{' '.join(new_words)}'."
            #   and initialize an iterator for the next word's length
            new_word_sizes.append(wordsize_iterator(1,17))
            word_size = next(new_word_sizes[-1])    # This shouldnt throw StopIteration
            #   as well as an empty exclude-list for the next word
            exclude_word_lists.append([])
            #   and reduce the remaining letters
            for char in current_word:
                remaining_original_letters = remaining_original_letters.replace(char, '', 1)
            info_str += f" Remaining letters: '{remaining_original_letters}'"
            logger.info(info_str)

        # if we have run through all word-sizes for the next word,
        # go back to finding the previous word
        except StopIteration:
            # Delete all variables for the next word (they depend on the previous word)
            exclude_word_lists.pop()
            new_word_sizes.pop()
            # Clean up for another previous word
            previous_word = new_words.pop()
            logger.info("Deleting word '" + previous_word + "' since no combination with the remaining letters was found.")
            remaining_original_letters += previous_word
            # Do not allow the same previous word again
            exclude_word_lists[-1].append(previous_word)
            logger.debug(f"exclude_words={exclude_word_lists[-1]};")
            logger.debug(f"new_words={new_words};")
            logger.debug(f"remaining_letters={remaining_original_letters};")

    return new_words

# low = 1, high = 16 for ca. most common word lengths
class SpiralIterator:
    def __init__(self, low, high):
        self.mid = (low + high + 1) // 2 - 1
        self.current = self.mid
        self.low = low
        self.high = high
        self.dir = -1
        self.dist = 0

    def __iter__(self):
        return self
    
    def __next__(self):
        self.current = self.mid + self.dir * self.dist
        if self.dir < 0:
            self.dist += 1
        self.dir *= -1
        if self.current < self.high and self.current >= self.low:
            return self.current
        raise StopIteration

def main():
    # Configuring logger
    log_level = logging.INFO

    log_formatter = logging.Formatter("%(asctime)s [%(threadName)-12.12s] [%(levelname)-5.5s]  %(message)s")
    root_logger = logging.getLogger()

    file_handler = logging.FileHandler("./challenge19.log")
    file_handler.setFormatter(log_formatter)
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    root_logger.addHandler(console_handler)

    root_logger.setLevel(log_level)

    # WordLists object
    wordlists_searcher = Wordlists(MAX_WORD_SIZE, merriam_webster_api_url, merriam_webster_api_key, root_logger)
    wordlists_searcher.init_wordlist(wordlist_file)
    wordlists_searcher.set_algorithm(Wordlists.BY_EXISTING)

    #orig_sentence = "Mary, had a little lamb27."
    if len(sys.argv) > 1:
        orig_sentence = sys.argv[1]
    else:
        orig_sentence = input("Enter a sentence to rearrange:\n")

    # How to iterate through word-sizes:
    #wordsize_iterator = lambda lo, hi: iter(range(lo, hi)) #using range
    wordsize_iterator = SpiralIterator # using custom spiral iterator

    start_time = time.time()
    new_words = build_new_sentence(orig_sentence, wordlists_searcher, root_logger, wordsize_iterator)
    elapsed_time = time.time() - start_time
    
    print(f"New sentence: '{' '.join(new_words)}'")
    print(f"Made {wordlists_searcher.n_api_requests} API requests.")
    print(f"Took {elapsed_time}s.")

if __name__ == "__main__":

    if "merriam_webster_api_key" not in globals() and "merriam_webster_api_key" not in locals():
        merriam_webster_api_key = input("No API key for merriam-webster API was hardcoded. Provide and API key or leave empty to not use API-lookup:\n")
    if merriam_webster_api_key == "":
        CHECK_WITH_API = False

    main()
