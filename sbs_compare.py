import os
import re
import difflib

import sublime
import sublime_plugin


def sbs_settings():
	return sublime.load_settings( 'SBSCompare.sublime-settings' )
	

class EraseViewCommand( sublime_plugin.TextCommand ):
	def run( self, edit ):
		self.view.erase( edit, sublime.Region( 0, self.view.size() ) )

class InsertViewCommand( sublime_plugin.TextCommand ):
	def run( self, edit, string='' ):
		self.view.insert( edit, self.view.size(), string )


class SbsLayoutPreserver( sublime_plugin.EventListener ):
	def count_views( self, ignore=None ):
		numCompare = 0
		numNotCompare = 0
		nonCompareWin = None
		for w in sublime.windows():
			for v in w.views():
				if ignore and v.id() == ignore:
					continue
				if v.settings().get( 'is_sbs_compare' ):
					numCompare += 1
				else:
					numNotCompare += 1
					nonCompareWin = w
			if len( w.views() ) == 0:
				nonCompareWin = w
		return { 'compare': numCompare, 'notCompare': numNotCompare, 'nonCompareWin': nonCompareWin }
	
	def on_pre_close( self, view ):
		# if one comparison view is closed, close the other
		if view.settings().get( 'is_sbs_compare' ):
			win = view.window()
			sublime.set_timeout( lambda: win.run_command( 'close_window' ), 10 )
			return
		
		# if there are no non-comparison views open after this closes...
		count = self.count_views( ignore=view.id() )
		if count['compare'] > 0 and count['notCompare'] == 0:
			last_file = view.file_name()
			
			# wait until the view is closed, then check again
			def after_close():
				# if there's no non-comparison window still open, make a new one
				# (there will be if the user only closes a tab!)
				count = self.count_views()		
				if count['nonCompareWin'] is None:
					sublime.active_window().run_command( 'new_window' )
					win = sublime.active_window()
					
					# attempt to restore sidebar and menu visibility
					if sbs_settings().get( 'toggle_sidebar', False ):
						win.run_command( 'toggle_side_bar' )
					if sbs_settings().get( 'toggle_menu', False ):
						win.run_command( 'toggle_menu' )
					
					# reopen last file
					if last_file is not None:
						win.open_file( last_file )
					
					sublime.set_timeout( lambda: win.show_quick_panel( [ 'Please close all comparison windows first' ], None ), 10 )
					
			sublime.set_timeout( after_close, 100 )
			
			
def generate_colour_scheme( view, generate=True ):
	# make sure we have hex AND we're >= ST3 (load_resource doesn't work in ST2)	
	colour_removed = sbs_settings().get( 'remove_colour', 'invalid.illegal' )
	colour_added = sbs_settings().get( 'add_colour', 'string' )
	colour_modified_deletion = sbs_settings().get( 'modified_colour_deletion', 'support.class' )
	colour_modified_addition = sbs_settings().get( 'modified_colour_addition', 'support.class' )
	colour_text = sbs_settings().get( 'text_colour', '' )
	
	notHex = False
	for col in [ colour_removed, colour_added, colour_modified_deletion, colour_modified_addition ]:
		if not '#' in col:
			notHex = True
	
	if int( sublime.version() ) < 3000 or notHex:
		return { 'removed': colour_removed, 'added': colour_added, 'modified_deletion': colour_modified_deletion, 'modified_addition': colour_modified_addition }
	
	
	# generate theme strings
	colourStrings = {}
	colourHexes = {}
	for col in [ [ 'removed', colour_removed ], [ 'added', colour_added ], [ 'modified_deletion', colour_modified_deletion ], [ 'modified_addition', colour_modified_addition ] ]:
		colourStrings[ col[0] ] = 'comparison.' + col[0]
		colourHexes[ col[0] ] = col[1]
	
	# relative for settings, absolute for writing to file
	# forwardSlashesOnly brought to you by Windows
	def theme_file( abs, folderOnly=False, forwardSlashesOnly=False ):
		package_dir = os.path.basename( sublime.packages_path() )
		if abs:
			package_dir = sublime.packages_path()
			
		folder = os.path.join( package_dir, 'User' )
		if forwardSlashesOnly:
			folder = folder.replace( '\\', '/' )
		if folderOnly:
			return folder
			
		file = os.path.join( folder, 'SBSCompare.tmTheme' )
		if forwardSlashesOnly:
			file = file.replace( '\\', '/' )
		return file
	
	# generate modified theme
	if generate:		
		# loop through colours and generate their xml
		xml = ''
		xml_tmpl = '<dict><key>name</key><string>{}</string><key>scope</key><string>{}</string><key>settings</key><dict><key>background</key><string>{}</string><key>foreground</key><string>{}</string></dict></dict>'
		
		for name in colourStrings:
			string = colourStrings[name]
			chex = colourHexes[name]
			xml += xml_tmpl.format( 'Comparison ' + name, string, chex, colour_text )
			
		# get current scheme xml
		current_scheme = view.settings().get( 'color_scheme' )  # no 'u' >:(
		scheme = sublime.load_resource( current_scheme )
		
		# combiiiiiiiiiiiiine
		dropzone = scheme.rfind( '</array>' )
		data = scheme[:dropzone] + xml + scheme[dropzone:]
		
		if not os.path.exists( theme_file( abs=True, folderOnly=True ) ):
			os.makedirs( theme_file( abs=True, folderOnly=True ) )
		
		# save new theme
		with open( theme_file( abs=True ), 'w', encoding='utf-8' ) as f:
			f.write( data )
	
	# set view to use new theme
	view.settings().set( 'color_scheme', theme_file( abs=False, forwardSlashesOnly=True ) )
	return colourStrings


sbs_markedSelection = [ '', '' ]
sbs_files = []
class SbsMarkSelCommand( sublime_plugin.TextCommand ):
	def run( self, edit ):
		global sbs_markedSelection
		
		window = sublime.active_window()
		view = window.active_view()
		sel = view.sel()

		region = sel[0]
		selectionText = view.substr( region )
		
		sbs_markedSelection[0] = sbs_markedSelection[1]
		sbs_markedSelection[1] = selectionText
		
class SbsCompareFilesCommand( sublime_plugin.ApplicationCommand ):
	def run( self, A=None, B=None ):
		global sbs_files
		
		if A == None or B == None:
			print( 'Compare Error: file(s) not specified' )
			return
			
		A = os.path.abspath( A )
		B = os.path.abspath( B )
		if not os.path.isfile( A ) or not os.path.isfile( B ):
			print( 'Compare Error: file(s) not found' )
			return
			
		del sbs_files[:]
		sbs_files.append( A )
		sbs_files.append( B )
		
		print( 'Comparing "%s" and "%s"' % ( A, B ) )
		
		window = sublime.active_window()
		window.run_command( 'sbs_compare' )

class SbsCompareCommand( sublime_plugin.TextCommand ):			
	def get_view_contents( self, view ):
		selection = sublime.Region( 0, view.size() )
		content = view.substr( selection )
		return content
		
	def close_view( self, view ):
		parent = view.window()
		parent.focus_view( view )
		parent.run_command( "close_file" )
		
	def get_drawtype( self ):
		# fill highlighting (DRAW_NO_OUTLINE) only exists on ST3+
		drawType = sublime.DRAW_OUTLINED
		if int( sublime.version() ) >= 3000:
			if not sbs_settings().get( 'outlines_only', False ):
				drawType = sublime.DRAW_NO_OUTLINE
		return drawType
		
	def highlight_lines( self, view, lines, sublines, col ):
		# full line diffs
		regionList = []
		markers = []
		for lineNum in lines:
			lineStart = view.text_point( lineNum, 0 )
			markers.append( lineStart )
			
			for sub in (sub for sub in sublines if sub[0] == lineNum):
				subStart = view.text_point( lineNum, sub[1] )
				subEnd = view.text_point( lineNum, sub[2] )
				
				region = sublime.Region( lineStart, subStart )
				regionList.append( region )
				
				lineStart = subEnd
				
			lineEnd = view.text_point( lineNum+1, -1 )
			region = sublime.Region( lineStart, lineEnd )
			regionList.append( region )
			
		colour = self.colours['removed']
		if col == 'B':
			colour = self.colours['added']

		drawType = self.get_drawtype()			
		view.add_regions( 'diff_highlighted-' + col, regionList, colour, '', drawType )
		view.settings().set( 'sbs_markers', markers )
		
	def sub_highlight_lines( self, view, lines, col ):
		# intra-line diffs
		regionList = []
		for diff in lines:
			lineNum = diff[0]
			start = view.text_point( lineNum, diff[1] )
			end = view.text_point( lineNum, diff[2] )
			
			region = sublime.Region( start, end )
			regionList.append( region )
		
		colour = self.colours['modified_deletion']
		if col == 'B':
			colour = self.colours['modified_addition']
			
		drawType = self.get_drawtype()			
		view.add_regions( 'diff_intraline-' + col, regionList, colour, '', drawType )
				
		
	def compare_views( self, view1, view2 ):
		view1_contents = self.get_view_contents( view1 )
		view2_contents = self.get_view_contents( view2 )
		
		linesA = view1_contents.splitlines( False )
		linesB = view2_contents.splitlines( False )
		
		if sbs_settings().get( 'ignore_whitespace', False ):
			view1_contents = re.sub( r'[ \t]', '', view1_contents )
			view2_contents = re.sub( r'[ \t]', '', view2_contents )
		
		diffLinesA = view1_contents.splitlines( False )
		diffLinesB = view2_contents.splitlines( False )
		
		bufferA = []
		bufferB = []
		
		highlightA = []
		highlightB = []
		
		subHighlightA = []
		subHighlightB = []
		
		diff = difflib.ndiff( diffLinesA, diffLinesB, charjunk = None )	
		
		hasDiffA = False
		hasDiffB = False
		intraLineA = ''
		intraLineB = ''
		hasIntraline = False
			
		lineNum = 0
		lineA = 0
		lineB = 0
		for line in diff:
			lineNum += 1
			code = line[:2]
			
			if code == '- ':
				bufferA.append( linesA[lineA] )
				bufferB.append( '' )
				highlightA.append( lineNum - 1 )
				intraLineA = linesA[lineA]
				hasDiffA = True
				lineA += 1
			elif code == '+ ':
				bufferA.append( '' )
				bufferB.append( linesB[lineB] )
				highlightB.append( lineNum - 1 )
				intraLineB = linesB[lineB]
				hasDiffB = True
				lineB += 1
			elif code == '  ':
				bufferA.append( linesA[lineA] )
				bufferB.append( linesB[lineB] )
				hasDiffA = False
				hasDiffB = False
				lineA += 1
				lineB += 1
			elif code == '? ':
				lineNum -= 1
				hasIntraline = True
			else:
				lineNum -= 1
				
			if hasIntraline and hasDiffA and hasDiffB:		
				if sbs_settings().get( 'enable_intraline', True ):
					s = difflib.SequenceMatcher( None, intraLineA, intraLineB )
					for tag, i1, i2, j1, j2 in s.get_opcodes():
						if tag != 'equal': # == replace
							lnA = lineNum-2
							lnB = lineNum-1
							
							if sbs_settings().get( 'intraline_emptyspace', False ):
								if tag == 'insert':
									i2 += j2 - j1
								if tag == 'delete':
									j2 += i2 - i1
							
							subHighlightA.append( [ lnA, i1, i2 ] )
							subHighlightB.append( [ lnB, j1, j2 ] )
				hasDiffA = False
				hasDiffB = False
				hasIntraline = False
									
						
		window = sublime.active_window()
		
		window.focus_view( view1 )
		window.run_command( 'erase_view' )
		window.run_command( 'insert_view', { 'string': '\n'.join( bufferA ) } )
		
		window.focus_view( view2 )
		window.run_command( 'erase_view' )
		window.run_command( 'insert_view', { 'string': '\n'.join( bufferB ) } )
		
		self.highlight_lines( view1, highlightA, subHighlightA, 'A' )			
		self.highlight_lines( view2, highlightB, subHighlightB, 'B' )
		
		intraDiff = ''
		if sbs_settings().get( 'enable_intraline', True ):
			self.sub_highlight_lines( view1, subHighlightA, 'A' )
			self.sub_highlight_lines( view2, subHighlightB, 'B' )
			
			numIntra = len( subHighlightA ) + len( subHighlightB )
			intraDiff =  str( numIntra ) + ' intra-line modifications\n'
		
		if sbs_settings().get( 'line_count_popup', False ):
			numDiffs = len( highlightA ) + len( highlightB )
			sublime.message_dialog( intraDiff + str( len( highlightA ) ) + ' lines removed, ' + str( len( highlightB ) ) + ' lines added\n' + str( numDiffs ) + ' line differences total' )

		
	def run( self, edit, with_active = False, group = -1, index = -1, compare_selections = False ):		
		global sbs_markedSelection, sbs_files
		
		active_view = self.view
		active_window = active_view.window()
		active_id = active_view.id()
		
		openTabs = []
		
		for view in active_window.views():
			if view.id() != active_id:
				viewName = 'untitled'
				if view.file_name():
					viewName = view.file_name()
				elif view.name():
					viewName = view.name()
				openTabs.append( [ viewName, view ] )

		def create_comparison( view1_contents, view2_contents, syntax, name1_override = False, name2_override = False ):
			view1_syntax = syntax
			view2_syntax = syntax
			
			# make new window
			active_window.run_command( 'new_window' )		
			new_window = sublime.active_window()
			new_window.set_layout( { "cols": [0.0, 0.5, 1.0], "rows": [0.0, 1.0], "cells": [[0, 0, 1, 1], [1, 0, 2, 1]] } )
			
			if sbs_settings().get( 'toggle_sidebar', False ):
				new_window.run_command( 'toggle_side_bar' )
			if sbs_settings().get( 'toggle_menu', False ):
				new_window.run_command( 'toggle_menu' )
			
			# view 1
			new_window.run_command( 'new_file' )
			new_window.run_command( 'insert_view', { 'string': view1_contents } )
			new_window.active_view().set_syntax_file( view1_syntax )
			
			view1_name = 'untitled'
			if active_view.file_name():
				view1_name = active_view.file_name()
			elif active_view.name():
				view1_name = active_view.name()
			if name1_override != False:
				view1_name = name1_override
			new_window.active_view().set_name( os.path.basename( view1_name ) + ' (active)' )
				
			new_window.active_view().set_scratch( True )	
			view1 = new_window.active_view()
			
			# view 2
			new_window.run_command( 'new_file' )
			new_window.run_command( 'insert_view', { 'string': view2_contents } )
			new_window.active_view().set_syntax_file( view2_syntax )
			new_window.active_view().set_name( os.path.basename( name2_override ) + ' (other)' )
			
			# move view 2 to group 2
			new_window.set_view_index( new_window.active_view(), 1, 0 )
			
			new_window.active_view().set_scratch( True )
			view2 = new_window.active_view()
			
			# keep track of these views			
			view1.settings().set( "is_sbs_compare", True )
			view2.settings().set( "is_sbs_compare", True )
			
			# disable word wrap
			view1.settings().set( 'word_wrap', 'false' )
			view2.settings().set( 'word_wrap', 'false' )
			
			# generate and set colour scheme
			self.colours = generate_colour_scheme( view1 )
			generate_colour_scheme( view2, generate=False )
			
			# run diff
			self.compare_views( view1, view2 )
			
			# make readonly
			new_window.focus_view( view1 )
			if sbs_settings().get( 'read_only', False ):
				new_window.active_view().set_read_only( True )
				
			new_window.focus_view( view2 )
			if sbs_settings().get( 'read_only', False ):
				new_window.active_view().set_read_only( True )
			
			# activate scroll syncer				
			ViewScrollSyncer( new_window, [ view1, view2 ] )
			
			# move views to top left
			view1.set_viewport_position( (0, 0), False )
			view2.set_viewport_position( (0, 0), False )
			
			# move cursors to top left
			origin = view.text_point( 0, 0 )
			
			view1.sel().clear()
			view1.sel().add( sublime.Region( origin ) )
			view1.show( origin )
			
			view2.sel().clear()
			view2.sel().add( sublime.Region( origin ) )
			view2.show( origin )
			
			# focus first view
			new_window.focus_view( view1 )

		def on_click( index ):
			if index > -1:
				# get original views' data
				view1_contents = self.get_view_contents( active_view )
				view2_contents = self.get_view_contents( openTabs[index][1] )
				
				syntax = active_view.settings().get( 'syntax' )
				
				create_comparison( view1_contents, view2_contents, syntax, False, openTabs[index][0] )
				
		def compare_from_views( view1, view2 ):
			if view1.is_loading() or view2.is_loading():
				sublime.set_timeout( lambda: compare_from_views( view1, view2 ), 10 )
			else:				
				view1_contents = self.get_view_contents( view1 )
				view2_contents = self.get_view_contents( view2 )
				syntax = view1.settings().get( 'syntax' )
				
				self.close_view( view1 )
				self.close_view( view2 )
				
				create_comparison( view1_contents, view2_contents, syntax, file1, file2 )

		if len( sbs_files ) > 0:
			file1 = sbs_files[0]
			file2 = sbs_files[1]
			
			view1 = active_window.open_file( file1 )
			view2 = active_window.open_file( file2 )
			
			compare_from_views( view1, view2 )			
			del sbs_files[:]
		elif compare_selections == True:
			selA = sbs_markedSelection[0]
			selB = sbs_markedSelection[1]
			
			sel = active_view.sel()
			
			selNum = 0
			for selection in sel:
				selNum += 1
			
			if selNum == 2:
				selA = active_view.substr( sel[0] )
				selB = active_view.substr( sel[1] )
			
			syntax = active_view.settings().get( 'syntax' )
			create_comparison( selA, selB, syntax, 'selection A', 'selection B' )
		elif len( openTabs ) == 1:
			on_click( 0 )
		else:
			if with_active == True:
				active_group, active_group_index = active_window.get_view_index( active_view )
				
				if group == -1 and index == -1:
					group = 0 if active_group == 1 else 1
					other_active_view = active_window.active_view_in_group( group )
					index = active_window.get_view_index( other_active_view )[1]
				
				if index > active_group_index:
					index -= 1
					
				if group > active_group:
					index += len( active_window.views_in_group( active_group ) ) - 1
				elif group < active_group:
					index += 1
					
				on_click( index )	
			else:
				menu_items = []
				for tab in openTabs:
					fileName = tab[0]
					if sbs_settings().get( 'expanded_filenames', False ):
						menu_items.append( [ os.path.basename( fileName ), fileName ] )
					else:
						menu_items.append( os.path.basename( fileName ) )	
				sublime.set_timeout( self.view.window().show_quick_panel( menu_items, on_click ) )


class ViewScrollSyncer( object ):
	def __init__( self, window, viewList ):
		self.window = window
		self.views = viewList
		self.timeout_focused = 10
		self.timeout_unfocused = 50
		
		self.run()
		
	def update_scroll( self, view1, view2, lastUpdated ):
		if lastUpdated == 'A':
			view2.set_viewport_position( view1.viewport_position(), False )
		elif lastUpdated == 'B':
			view1.set_viewport_position( view2.viewport_position(), False )
		
	def run( self ):
		if not self.window:
			return
		
		if self.window.id() != sublime.active_window().id():
			sublime.set_timeout( self.run, self.timeout_unfocused )
			return
			
		view1 = self.views[0]
		view2 = self.views[1]
		
		if not view1 or not view2:
			return
		
		vecA = view1.viewport_position()
		vecB = view2.viewport_position()
		
		if vecA != vecB:
			lastVecA0 = view1.settings().get( 'viewsync_last_vec0', 1 )
			lastVecA1 = view1.settings().get( 'viewsync_last_vec1', 1 )
			
			lastVecB0 = view2.settings().get( 'viewsync_last_vec0', 1 )
			lastVecB1 = view2.settings().get( 'viewsync_last_vec1', 1 )
			
			lastVecA = ( lastVecA0, lastVecA1 )
			lastVecB = ( lastVecB0, lastVecB1 )
			
			lastUpdated = ''
			
			if lastVecA != vecA:				
				lastUpdated = 'A'
				
				view1.settings().set( 'viewsync_last_vec0', vecA[0] )
				view1.settings().set( 'viewsync_last_vec1', vecA[1] )
				
				view2.settings().set( 'viewsync_last_vec0', vecA[0] )
				view2.settings().set( 'viewsync_last_vec1', vecA[1] )				
				
			if lastVecB != vecB:				
				lastUpdated = 'B'
				
				view1.settings().set( 'viewsync_last_vec0', vecB[0] )
				view1.settings().set( 'viewsync_last_vec1', vecB[1] )
				
				view2.settings().set( 'viewsync_last_vec0', vecB[0] )
				view2.settings().set( 'viewsync_last_vec1', vecB[1] )
				
			if ( lastUpdated != '' ):
				self.update_scroll( view1, view2, lastUpdated )
		
		sublime.set_timeout( self.run, self.timeout_focused )

					
def sbs_scroll_to( view, prev=False ):
	current_pos = view.sel()[0].begin()
	for col in [ 'A', 'B' ]:
		regions = view.settings().get( 'sbs_markers' )
		if prev:
			regions.reverse()
		
		for highlight in regions:			
			found = False
			if prev:
				if highlight < current_pos:
					found = True
			else:
				if highlight > current_pos:
					found = True
					
			if found:
				view.sel().clear()
				view.sel().add( sublime.Region( highlight ) )
				view.show( highlight )
				
				# sometimes necessary, better safe than sorry
				sublime.set_timeout( lambda: view.show( highlight ), 10 )
				return
				
	msg = 'Reached the '
	msg += 'beginning' if prev else 'end'
	view.window().show_quick_panel( [ msg ], None )		
					
class SbsPrevDiffCommand( sublime_plugin.TextCommand ):
	def run( self, edit, string='' ):
		sbs_scroll_to( self.view, prev=True )
					
class SbsNextDiffCommand( sublime_plugin.TextCommand ):
	def run( self, edit, string='' ):
		sbs_scroll_to( self.view )
