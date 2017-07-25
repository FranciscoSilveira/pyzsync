import hashlib

"""
Used by the system with an unpatched file upon receiving a hash blueprint of the patched file
Receives an input stream and set of hashes for a patched file
Returns a dictionary of checksums in the form of:
{
	weak : [(remote_byte, strong), (...)]             <= When a remote block has no local match
	weak : [(remote_byte, strong, local_byte), (...)] <= When a remote block matched a local block
}
Example:
{
	3259370527 : [(2432, b'\xb7w\x9d\x1a\x1f\x89b\x84\x06[\xf48\xacl\x8a\xb6', 2427)]
	2889812746 : [(77, b'\xc8\x83 $\xa0\x1dXKs\x1c\x80\xa2\xdaDgH')] 
}
"""
DEFAULT_BLOCKSIZE = 4096

def zsync_delta(datastream, remotesignatures, blocksize=DEFAULT_BLOCKSIZE):
	remote_hashes = {}
	num_blocks = 0
	for block, (weak, strong) in enumerate(remotesignatures):
		num_blocks += 1
		if weak in remote_hashes:
			remote_hashes[weak].append((block, strong))
		else:
			remote_hashes[weak] = [(block, strong)]

	match = True
	local_offset = -blocksize

	while True:
		if match and datastream is not None:
			# Whenever there is a match or the loop is running for the first
			# time, populate the window using weakchecksum instead of rolling
			# through every single byte which takes at least twice as long.
			window = bytearray(datastream.read(blocksize))
			local_offset += blocksize
			checksum, a, b = weakchecksum(window)
		if checksum in remote_hashes:
			# Matched the weak hash
			local_strong = hashlib.md5(window).digest()
			for index,(remote_offset,remote_strong) in enumerate(remote_hashes[checksum]):
				if local_strong == remote_strong:
					# Found a matching block, insert the local file's byte offset in the results
					remote_hashes[checksum][index] = (remote_offset, local_strong, local_offset)
					match = True
					# Don't try to match any other blocks with this one
					break

			if datastream.closed:
				break

		else:
			# The weakchecksum (or the strong one) did not match
			match = False
			try:
				if datastream:
					# Get the next byte and affix to the window
					newbyte = ord(datastream.read(1))
					window.append(newbyte)
			except TypeError:
				# No more data from the file; the window will slowly shrink.
				# newbyte needs to be zero from here on to keep the checksum
				# correct.
				newbyte = 0
				tailsize = datastream.tell() % blocksize
				datastream = None

			if datastream is None and len(window) <= tailsize:
				# The likelihood that any blocks will match after this is
				# nearly nil so call it quits.
				break

			# Yank off the extra byte and calculate the new window checksum
			# This is maintaining the old contents inside the bytearray, and just adusting the offset
			oldbyte = window.pop(0)
			local_offset += 1
			checksum, a, b = rollingchecksum(oldbyte, newbyte, a, b, blocksize)

	# Order the results into a proper blueprint+requestlist tuple and return it
	return get_instructions(remote_hashes, blocksize, num_blocks)

def get_instructions(remote_hashes, blocksize, num_blocks):
	instructions = [None] * num_blocks
	to_request = []
	for weak in remote_hashes:
		for block in remote_hashes[weak]:
			remote_block = block[0]
			strong = block[1]
			if len(block) == 2:
				# Not found
				instructions[remote_block] = (remote_block*blocksize, weak, strong)
				to_request.append(remote_block)
			else:
				# Found
				local_block = block[2]
				instructions[remote_block] = local_block
	return instructions, to_request

def block_checksums(instream, blocksize=DEFAULT_BLOCKSIZE):
	"""
	Generator of (weak hash (int), strong hash(bytes)) tuples
	for each block of the defined size for the given data stream.
	"""
	read = instream.read(blocksize)

	while read:
		yield (weakchecksum(read)[0], hashlib.md5(read).digest())
		read = instream.read(blocksize)

def rollingchecksum(removed, new, a, b, blocksize=DEFAULT_BLOCKSIZE):
	"""
	Generates a new weak checksum when supplied with the internal state
	of the checksum calculation for the previous window, the removed
	byte, and the added byte.
	"""
	a -= removed - new
	b -= removed * blocksize - a
	return (b << 16) | a, a, b

def get_blocks(datastream, requests, blocksize=DEFAULT_BLOCKSIZE):
	#blocks = []
	for block in requests:
		datastream.seek(block*blocksize)
		content = datastream.read(blocksize)
		#blocks.append((offset, block))
		yield (block, content)

def merge_instructions_blocks(instructions, blocks, blocksize=DEFAULT_BLOCKSIZE):
	for (block, content) in blocks:
		offset = block*blocksize
		if instructions[block][0] == offset:
			instructions[block] = content
	return instructions

def patchstream(instream, outstream, delta, blocksize=DEFAULT_BLOCKSIZE):
	"""
	Patches instream using the supplied delta and write the resulting
	data to outstream.
	"""

	for element in delta:
		if isinstance(element, int) and blocksize:
			instream.seek(element)
			element = instream.read(blocksize)
		outstream.write(element)

def weakchecksum(data):
	"""
	Generates a weak checksum from an iterable set of bytes.
	"""
	a = b = 0
	l = len(data)
	for i in range(l):
		a += data[i]
		b += (l - i) * data[i]

	return (b << 16) | a, a, b